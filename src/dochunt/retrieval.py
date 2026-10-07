"""Bounded public HTTP retrieval, validating and pinning every redirect hop."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from .extraction import extract
import base64
import json
import subprocess
import sys
import http.client
import ipaddress
import socket
import ssl
from urllib.parse import urlsplit, urljoin, quote
from .models import public_url
from .budget import bounded_call


DNS_TIMEOUT_SECONDS = 5
HTTP_TIMEOUT_SECONDS = 15


class RetrievalTimeout(TimeoutError):
    """The isolated operation has definitely exited or been killed and reaped."""


class RetrievalBlocked(ValueError):
    pass


@dataclass
class FetchResponse:
    status: int
    headers: dict[str,str]
    body: bytes


@dataclass
class Retrieved:
    url: str
    content: str | None
    coverage: str
    retrieved_at: str = ''
    content_hash: str = ''
    format: str = ''
    title: str = ''
    sections: list[dict] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    provenance: dict = field(default_factory=dict)
    content_type: str = ''


def ordered_addresses(addresses):
    # Stable across resolver ordering and process hash seeds. Prefer IPv4 so a
    # host without an IPv6 route can still reach ordinary dual-stack sites.
    return sorted(set(addresses),key=lambda a:(ipaddress.ip_address(a).version,int(ipaddress.ip_address(a))))


def resolve(host):
    return ordered_addresses(item[4][0] for item in socket.getaddrinfo(host,None,type=socket.SOCK_STREAM))


def connect(url, ip, timeout, cap):
    parsed=urlsplit(url)
    port=parsed.port or (443 if parsed.scheme=='https' else 80)
    connection=http.client.HTTPConnection(parsed.hostname,port,timeout=timeout)
    # Connect to the validated address, preserving Host and TLS SNI hostname.
    sock=socket.create_connection((ip,port),timeout=timeout)
    if parsed.scheme=='https':
        try: sock=ssl.create_default_context().wrap_socket(sock,server_hostname=parsed.hostname)
        except BaseException:
            sock.close()
            raise
    connection.sock=sock
    try:
        target=quote(parsed.path or '/',safe="/%:@!$&'()*+,;=-._~")
        if parsed.query: target+='?'+quote(parsed.query,safe="/?%:@!$&'()*+,;=-._~[]")
        connection.request('GET',target,headers={'User-Agent':'DocHunt/0.1','Accept':'text/plain, text/html, application/json, application/pdf, text/csv, application/rss+xml;q=0.5, application/atom+xml;q=0.5, application/xml;q=0.3','Accept-Encoding':'identity'})
        result=connection.getresponse()
        return FetchResponse(result.status,{k.lower():v for k,v in result.getheaders()},result.read(cap+1))
    finally: connection.close()


def isolated_operation(operation,args,timeout,*,command=None):
    """Run one cancellable operation; run() kills AND waits before timeout returns.

    The default worker performs no subprocess work, so it leaves no descendants.
    Only this confirmed-ended path may preserve the shared research budget.
    """
    if timeout<=0: raise RetrievalTimeout('Public operation deadline exhausted')
    job=json.dumps({'operation':operation,'args':args,'timeout':timeout}).encode()
    if len(job)>16000: raise OSError('Public operation input too large')
    try:
        result=subprocess.run(command or [sys.executable,'-m','dochunt.retrieval_worker'],input=job,capture_output=True,timeout=timeout,check=False)
    except subprocess.TimeoutExpired as exc:
        # subprocess.run has killed the child and waited for termination here.
        raise RetrievalTimeout('Public worker terminated at deadline') from exc
    if result.returncode or len(result.stdout)>1_500_000: raise OSError('Public worker unavailable')
    try:
        data=json.loads(result.stdout)
        if data=={'error':'timeout'}: raise RetrievalTimeout('Public socket operation ended at deadline')
        if not isinstance(data,dict) or set(data)!={'result'}: raise ValueError()
        value=data['result']
        if operation=='resolve':
            if not isinstance(value,list) or not 1<=len(value)<=100 or any(not isinstance(a,str) for a in value): raise ValueError()
            return value
        if operation!='connect' or not isinstance(value,dict) or set(value)!={'status','headers','body'} or type(value['status']) is not int or not isinstance(value['headers'],dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in value['headers'].items()) or not isinstance(value['body'],str): raise ValueError()
        body=base64.b64decode(value['body'],validate=True)
        if len(body)>args[3]+1: raise ValueError()
        return FetchResponse(value['status'],value['headers'],body)
    except (ValueError,TypeError,KeyError): raise OSError('Malformed public worker result') from None


class PublicRetriever:
    def __init__(self,budget,resolver=resolve,connector=connect,max_bytes=1_000_000,max_redirects=3,*,operation_runner=isolated_operation):
        if type(max_bytes) is not int or not 1 <= max_bytes <= 1_000_000: raise ValueError('Invalid size cap')
        if type(max_redirects) is not int or not 0 <= max_redirects <= 3: raise ValueError('Invalid redirect cap')
        self.budget,self.resolver,self.connector=budget,resolver,connector
        self.max_bytes,self.max_redirects=max_bytes,max_redirects
        self.operation_runner=operation_runner

    def operation(self,kind,function,timeout,*args):
        if (kind=='resolve' and function is resolve) or (kind=='connect' and function is connect):
            return self.operation_runner(kind,args,timeout)
        # Arbitrary custom callbacks cannot safely be serialized/cancelled. Keep
        # the existing fail-closed daemon timeout policy for that extension path.
        return bounded_call(function,timeout,*args)

    def fetch(self,url,*,reserve_fetches=0):
        if type(reserve_fetches) is not int or reserve_fetches<0: raise ValueError('Invalid fetch reserve')
        for hop in range(self.max_redirects+1):
            try:
                public_url(url)
                p=urlsplit(url)
                if p.port not in (None,80 if p.scheme=='http' else 443): raise ValueError('Nonstandard port')
                addresses=self.operation('resolve',self.resolver,min(DNS_TIMEOUT_SECONDS,self.budget.remaining_seconds),p.hostname)
                if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
                    raise ValueError('Nonpublic destination')
            except RetrievalTimeout:
                self.budget.remaining_seconds
                return Retrieved(url,None,'Document inaccessible: DNS worker ended at deadline')
            except TimeoutError as exc:
                self.budget.blocked=True
                raise RetrievalBlocked('DNS deadline exhausted') from exc
            except (ValueError,TypeError,OSError) as exc:
                raise RetrievalBlocked('Public destination validation failed') from exc
            # All DNS results passed validation above, including addresses we
            # will not try. At most one address from each family may be used.
            candidates=[]; families=set()
            for address in ordered_addresses(addresses):
                family=ipaddress.ip_address(address).version
                if family not in families:
                    candidates.append(address); families.add(family)
            http_deadline=self.budget.clock()+min(HTTP_TIMEOUT_SECONDS,self.budget.remaining_seconds)
            coverage='Document inaccessible: network or TLS failure'
            response=None
            for address in candidates:
                timeout=min(http_deadline-self.budget.clock(),self.budget.remaining_seconds)
                if timeout<=0: break
                if reserve_fetches and self.budget.remaining('fetches')<=reserve_fetches:
                    return Retrieved(url,None,'Document inaccessible: final retrieval fetch reserve retained')
                self.budget.consume(fetches=1)
                try: response=self.operation('connect',self.connector,timeout,url,address,timeout,self.max_bytes)
                except RetrievalTimeout:
                    self.budget.remaining_seconds
                    coverage='Document inaccessible: HTTP worker ended at deadline'
                    continue
                except TimeoutError:
                    self.budget.blocked=True
                    return Retrieved(url,None,'Document inaccessible: client deadline exhausted')
                except (OSError,http.client.HTTPException):
                    self.budget.remaining_seconds
                    coverage='Document inaccessible: network or TLS failure'
                    continue
                self.budget.remaining_seconds
                break
            if response is None: return Retrieved(url,None,coverage)
            if len(response.body)>self.max_bytes: raise RetrievalBlocked('Document exceeds size cap')
            if response.status in (301,302,303,307,308):
                if hop==self.max_redirects or 'location' not in response.headers: raise RetrievalBlocked('Redirect limit or missing location')
                url=urljoin(url,response.headers['location'])
                continue
            mime=response.headers.get('content-type','').split(';')[0].strip().lower()
            if response.status != 200: return Retrieved(url,None,f'Document inaccessible: HTTP {response.status}',content_type=mime)
            if response.headers.get('content-encoding','identity').lower() not in ('','identity'):
                raise RetrievalBlocked('Compressed body is unsupported')
            parsed=extract(response.body,mime,url,min(10,self.budget.remaining_seconds))
            if not parsed or not any(section['text'] for section in parsed['sections']):
                unsupported_format='pdf' if mime=='application/pdf' or response.body.lstrip().startswith(b'%PDF-') else (parsed.get('format','') if parsed else '')
                return Retrieved(url,None,'Document inaccessible: unsupported, invalid or scanned format; OCR/browser extraction unavailable',format=unsupported_format,content_type=mime)
            self.budget.remaining_seconds
            content=' '.join(section['text'] for section in parsed['sections'])
            receipt=datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z')
            return Retrieved(url,content,'Public document retrieved',receipt,sha256(response.body).hexdigest(),parsed['format'],parsed['title'],parsed['sections'],parsed['links'],content_type=mime)
        raise RetrievalBlocked('Redirect limit')


def document_identity(url):
    """Canonical identity for de-duplicating document URLs (tracking parameters removed)."""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
    p = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if not k.casefold().startswith('utm_') and k.casefold() not in {'gclid', 'fbclid'}]
    return urlunsplit((p.scheme.casefold(), p.netloc.casefold(), p.path or '/', urlencode(query), '')).rstrip('/')
