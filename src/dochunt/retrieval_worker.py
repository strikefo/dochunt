"""One isolated public DNS/HTTP operation. No model, credentials or child workers."""
import base64
import ipaddress
import json
import sys
from urllib.parse import urlsplit
from .models import public_url
from .retrieval import resolve, connect


def main():
    try:
        raw=sys.stdin.buffer.read(16001)
        if len(raw)>16000: raise ValueError()
        job=json.loads(raw)
        if set(job)!={'operation','args','timeout'} or not isinstance(job['args'],list) or not isinstance(job['timeout'],(int,float)) or not 0<job['timeout']<=15: raise ValueError()
        if job['operation']=='resolve' and len(job['args'])==1:
            result=resolve(job['args'][0])
            if len(result)>100: raise ValueError()
        elif job['operation']=='connect' and len(job['args'])==4:
            url,ip,timeout,cap=job['args']
            public_url(url); parsed=urlsplit(url)
            if parsed.port not in (None,80 if parsed.scheme=='http' else 443) or not ipaddress.ip_address(ip).is_global or type(cap) is not int or not 1<=cap<=1_000_000 or not isinstance(timeout,(int,float)) or not 0<timeout<=15: raise ValueError()
            response=connect(url,ip,timeout,cap)
            if len(response.body)>cap+1 or sum(len(k)+len(v) for k,v in response.headers.items())>64000: raise ValueError()
            result={'status':response.status,'headers':response.headers,'body':base64.b64encode(response.body).decode('ascii')}
        else: raise ValueError()
        print(json.dumps({'result':result}))
    except TimeoutError: print(json.dumps({'error':'timeout'}))
    except Exception: print(json.dumps({'error':'unavailable'}))


if __name__=='__main__': main()
