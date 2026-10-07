"""Optional, bounded fresh-page fallback. No SDK or hosted connector required."""
from datetime import datetime, timezone
from hashlib import sha256
import ipaddress
import json
import os
from pathlib import PurePosixPath
import subprocess
import sys
from urllib.parse import urlsplit
from .budget import BudgetExceeded, bounded_call
from .extraction import extract, normalized
from .models import public_url
from .retrieval import Retrieved, RetrievalBlocked, RetrievalTimeout, DNS_TIMEOUT_SECONDS

ENDPOINT=('api.firecrawl.dev','/v2/scrape')
MAX_RESPONSE_BYTES=1_000_000


def check_environment(config,environ=None):
    if not config.retrieval.firecrawl_enabled: return None
    key=(os.environ if environ is None else environ).get('FIRECRAWL_API_KEY','')
    if not isinstance(key,str) or not key.strip() or any(ord(c)<33 or ord(c)>126 for c in key):
        raise ValueError('Enabled page fallback requires FIRECRAWL_API_KEY in secret storage')
    return key


class FirecrawlHTTPTransport:
    """Single dispatch; timeout kills and reaps the client worker, never retries."""
    def __call__(self,endpoint,headers,payload,timeout,cap):
        if endpoint!=ENDPOINT: raise ValueError('Invalid scrape endpoint')
        job=json.dumps({'key':headers['Authorization'].removeprefix('Bearer '),
                        'payload':payload,'timeout':timeout,'cap':cap}).encode()
        if len(job)>20000: raise ValueError('Scrape request too large')
        try:
            result=subprocess.run([sys.executable,'-m','dochunt.firecrawl_worker'],input=job,
                                  capture_output=True,timeout=timeout,check=False)
        except subprocess.TimeoutExpired:
            raise RetrievalTimeout('Scrape worker terminated at deadline') from None
        if result.returncode or len(result.stdout)>cap+10000: raise OSError('Scrape unavailable')
        try:
            response=json.loads(result.stdout)
            if set(response)!={'result'}: raise ValueError()
            return response['result']
        except (ValueError,TypeError): raise OSError('Scrape response rejected') from None


class FirecrawlRetriever:
    def __init__(self,direct,settings,key,transport=None):
        self.direct,self.budget,self.settings,self.key=direct,direct.budget,settings,key
        self.transport=transport or FirecrawlHTTPTransport()
        self.attempts=[]

    def validate_url(self,url):
        public_url(url)
        p=urlsplit(url)
        if p.port not in (None,80 if p.scheme=='http' else 443): raise RetrievalBlocked('Nonstandard source port')
        addresses=self.direct.operation('resolve',self.direct.resolver,
                                       min(DNS_TIMEOUT_SECONDS,self.budget.remaining_seconds),p.hostname)
        if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
            raise RetrievalBlocked('Nonpublic scrape destination')

    def fetch(self,url,*,reserve_fetches=0,independent=False):
        original=self.direct.fetch(url,reserve_fetches=reserve_fetches)
        if original.content: return original
        # Direct unsafe failures raise before reaching here. PDF processing stays local.
        if (PurePosixPath(urlsplit(original.url).path).suffix.lower()=='.pdf' or original.format=='pdf'
                or (original.content_type and original.content_type not in ('text/html','application/xhtml+xml','text/plain'))):
            return original
        reserve=0 if independent else min(4,self.settings.firecrawl_max_calls//2)
        if len(self.attempts)>=self.settings.firecrawl_max_calls-reserve:
            original.coverage+='; Firecrawl attempt cap/reserve retained'
            return original
        if self.budget.remaining('fetches')<=reserve_fetches: return original
        timeout=min(self.settings.firecrawl_timeout_seconds,self.budget.remaining_seconds)
        if timeout<1: return original
        try: self.validate_url(original.url)
        except RetrievalTimeout: return original
        except TimeoutError:
            self.budget.blocked=True
            raise BudgetExceeded('Scrape destination validation timed out') from None
        except (ValueError,TypeError,OSError): raise RetrievalBlocked('Scrape public destination rejected') from None
        timeout=min(timeout,self.budget.remaining_seconds)
        if timeout<1: return original
        payload={'url':original.url,'formats':['rawHtml','markdown','links'],'maxAge':0,
                 'parsers':[],'onlyMainContent':True,
                 'skipTlsVerification':False,'storeInCache':False,'proxy':'enhanced',
                 'timeout':int(timeout*1000)}
        self.budget.consume(fetches=1)
        attempt={'url':original.url,'outcome':'unknown','independent':independent}
        self.attempts.append(attempt)
        try:
            args=(ENDPOINT,{'Authorization':'Bearer '+self.key,'Content-Type':'application/json'},payload,timeout,MAX_RESPONSE_BYTES)
            response=self.transport(*args) if type(self.transport) is FirecrawlHTTPTransport else bounded_call(self.transport,timeout,*args)
            self.budget.remaining_seconds
            receipt=self.parse(response,original.url)
            attempt['outcome']='retrieved'
            return receipt
        except RetrievalTimeout:
            attempt['outcome']='timeout'
        except TimeoutError:
            self.budget.blocked=True
            attempt['outcome']='unknown timeout'
            raise BudgetExceeded('Scrape cancellation uncertain; budget blocked') from None
        except BudgetExceeded: raise
        except Exception:
            attempt['outcome']='rejected'
        original.coverage+='; Firecrawl fallback unavailable ('+attempt['outcome']+')'
        return original

    def parse(self,response,requested):
        if len(json.dumps(response).encode())>MAX_RESPONSE_BYTES or not isinstance(response,dict) or response.get('success') is not True:
            raise ValueError('Invalid scrape response')
        data=response.get('data'); meta=data.get('metadata') if isinstance(data,dict) else None
        if not isinstance(meta,dict) or type(meta.get('statusCode')) is not int or meta['statusCode']!=200 or meta.get('error'):
            raise ValueError('Unavailable source')
        if meta.get('sourceURL')!=requested: raise ValueError('Source binding mismatch')
        reported=meta.get('url',requested)
        self.validate_url(reported)
        mime=meta.get('contentType','text/html').split(';')[0].lower().strip()
        if mime not in ('text/html','application/xhtml+xml','text/plain') or data.get('rawBase64') or any(meta.get(k,1)!=1 for k in ('numPages','totalPages')):
            raise ValueError('Unsupported page')
        raw=data.get('rawHtml')
        if isinstance(raw,str) and raw.strip():
            body=raw.encode(); parsed=extract(body,'text/html',reported,min(10,self.budget.remaining_seconds))
            basis='firecrawl.rawHtml.utf8'
        else:
            raw=data.get('markdown')
            if not isinstance(raw,str) or not raw.strip(): raise ValueError('Empty page')
            body=raw.encode(); parsed={'format':'markdown','title':meta.get('title',''),
                                     'sections':[{'locator':'Firecrawl markdown','text':normalized(raw)[:120000]}],'links':[]}
            basis='firecrawl.markdown.utf8'
        if len(body)>MAX_RESPONSE_BYTES or not parsed or not any(s['text'] for s in parsed['sections']): raise ValueError('Empty/oversized page')
        links=data.get('links',[])
        if not isinstance(links,list): raise ValueError('Invalid links')
        for link in links:
            if not isinstance(link,str): continue
            try: public_url(link)
            except ValueError: continue
            if link not in parsed['links'] and len(parsed['links'])<100: parsed['links'].append(link)
        now=datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z')
        provenance={'provider':'firecrawl','api_version':'v2','hash_basis':basis,'requested_url':requested,
                    'reported_url':reported,'source_status':200,'received_at':now,'max_age':0}
        return Retrieved(reported,' '.join(s['text'] for s in parsed['sections']),
                         'Public page retrieved through Firecrawl; hash covers provider-returned text',
                         now,sha256(body).hexdigest(),parsed['format'],parsed['title'],parsed['sections'],parsed['links'],provenance)


def main():
    """Readiness only: validates configuration/key presence, sends no requests."""
    import argparse
    from pathlib import Path
    from .config import load_config
    parser=argparse.ArgumentParser(description='Check optional page fallback without network calls')
    parser.add_argument('--check',type=Path,required=True,metavar='CONFIG')
    args=parser.parse_args()
    try:
        config=load_config(args.check)
        check_environment(config)
    except ValueError as exc:
        parser.exit(1,str(exc)+'\n')
    print('Firecrawl fallback ready (no request sent).' if config.retrieval.firecrawl_enabled else 'Firecrawl fallback disabled.')

def validate_provenance(receipt):
    provenance=receipt.get('provenance',{})
    if not provenance: return
    from .models import public_url
    required={'provider','api_version','hash_basis','requested_url','reported_url','source_status','received_at','max_age'}
    if not isinstance(provenance,dict) or set(provenance)!=required or provenance['provider']!='firecrawl' or provenance['api_version']!='v2':
        raise ValueError('Invalid retrieval provenance')
    for key in ('requested_url','reported_url'): public_url(provenance[key])
    basis={'html':'firecrawl.rawHtml.utf8','markdown':'firecrawl.markdown.utf8'}.get(receipt.get('format'))
    if not basis or provenance['hash_basis']!=basis or provenance['reported_url']!=receipt['url'] or provenance['received_at']!=receipt['retrieved_at'] or type(provenance['source_status']) is not int or provenance['source_status']!=200 or type(provenance['max_age']) is not int or provenance['max_age']!=0:
        raise ValueError('Evidence differs from retrieval provenance')


if __name__=='__main__': main()
