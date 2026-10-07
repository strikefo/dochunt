"""One fixed-endpoint scrape; credentials arrive on stdin, never argv/logs."""
import http.client
import json
import sys


def main():
    result={'error':'unavailable'}
    connection=None
    try:
        job=json.loads(sys.stdin.buffer.read(20000))
        connection=http.client.HTTPSConnection('api.firecrawl.dev',timeout=job['timeout'])
        connection.request('POST','/v2/scrape',json.dumps(job['payload']).encode(),
                           {'Authorization':'Bearer '+job['key'],'Content-Type':'application/json'})
        response=connection.getresponse()
        body=response.read(job['cap']+1)
        if response.status==200 and len(body)<=job['cap']:
            result={'result':json.loads(body)}
        else: result={'error':'response rejected'}
    except Exception:
        pass  # Remote errors and credentials must never enter logs.
    finally:
        if connection: connection.close()
    sys.stdout.write(json.dumps(result))

if __name__=='__main__': main()
