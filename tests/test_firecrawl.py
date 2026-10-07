from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
import time
import pytest
from dochunt.budget import Budget, BudgetExceeded
from dochunt.config import Retrieval, load_config, config_from_data
from dochunt.firecrawl import FirecrawlRetriever, check_environment, ENDPOINT
from dochunt.retrieval import PublicRetriever, FetchResponse, RetrievalBlocked, RetrievalTimeout
from dochunt.models import Limits, Evidence
from dochunt.firecrawl import validate_provenance

URL='https://agency.example/records'

def page(url=URL,raw='<main><h1>Official filing</h1><p>File a request for case records.</p><a href="/submit">Submit request</a></main>'):
    return {'success':True,'data':{'rawHtml':raw,'metadata':{'sourceURL':url,'statusCode':200,'contentType':'text/html'}}}

def retriever(response=None,**kwargs):
    budget=Budget(Limits())
    direct=PublicRetriever(budget,resolver=lambda host:['93.184.216.34'],connector=lambda *a:FetchResponse(403,{},b''))
    calls=[]
    def transport(*args):
        calls.append(args)
        return page() if response is None else response
    fallback=FirecrawlRetriever(direct,Retrieval(True), 'mock-firecrawl-key', transport)
    return fallback,calls


def test_direct_failure_returns_exact_provider_passages_and_honest_hash():
    f,calls=retriever(); receipt=f.fetch(URL)
    assert receipt.content=='Official filing File a request for case records. Submit request'
    assert receipt.links==['https://agency.example/submit']
    assert f.budget.usage.fetches==2 and len(f.attempts)==1
    endpoint,headers,payload,timeout,cap=calls[0]
    assert endpoint==ENDPOINT and headers['Authorization']=='Bearer mock-firecrawl-key'
    assert payload['maxAge']==0 and payload['parsers']==[] and not payload['skipTlsVerification']
    assert payload['formats']==['rawHtml','markdown','links'] and payload['proxy']=='enhanced'
    assert set(payload)=={'url','formats','maxAge','parsers','onlyMainContent','skipTlsVerification','storeInCache','proxy','timeout'}
    assert 'cleanMarkdown' not in payload
    assert timeout<=30 and cap==1_000_000
    validate_provenance(asdict(receipt))
    bad=asdict(receipt); bad['provenance']['hash_basis']='original_http_bytes'
    with pytest.raises(ValueError,match='provenance'): validate_provenance(bad)


def test_direct_success_never_dispatches_fallback():
    f,calls=retriever(); f.direct.connector=lambda *a:FetchResponse(200,{'content-type':'text/html'},b'<main>Actual original page</main>')
    receipt=f.fetch(URL)
    assert receipt.content=='Actual original page' and not calls and not f.attempts


def test_disabled_and_missing_key_fail_before_dispatch(monkeypatch):
    c=load_config(Path('examples/education.toml'))
    assert check_environment(c,{}) is None and 'retrieval' not in c.secret_names
    c.retrieval.firecrawl_enabled=True
    with pytest.raises(ValueError,match='FIRECRAWL_API_KEY'): check_environment(c,{})
    assert check_environment(c,{'FIRECRAWL_API_KEY':'mock-key'})=='mock-key'
    from dochunt.providers.base import FixtureTransport
    from dochunt.providers.openai import OpenAIProvider
    p=OpenAIProvider(c.ai.model,Budget(c.limits),FixtureTransport([]))
    monkeypatch.delenv('FIRECRAWL_API_KEY',raising=False)
    from dochunt.engine import fetcher
    with pytest.raises(ValueError,match='FIRECRAWL_API_KEY'): fetcher(c,p)
    assert not p.transport.requests


def test_caps_reserve_independent_attempts_and_charge_failures():
    f,calls=retriever({'success':False})
    for i in range(7): f.fetch(URL)
    assert len(calls)==4
    for i in range(5): f.fetch(URL,independent=True)
    assert len(calls)==8 and all(a['outcome']=='rejected' for a in f.attempts)
    assert f.budget.usage.fetches==20  # 12 direct attempts plus 8 scrape attempts


def test_fetch_reserve_and_deadline_prevent_paid_dispatch():
    f,calls=retriever(); f.budget.usage.fetches=f.budget.limits.fetches-1
    assert not f.fetch(URL,reserve_fetches=1).content and not calls
    f,calls=retriever(); f.budget.started-=1499.5
    assert not f.fetch(URL).content and not calls


@pytest.mark.parametrize('change',[
    lambda r:r.update(success=False),
    lambda r:r['data']['metadata'].update(statusCode=403),
    lambda r:r['data']['metadata'].update(sourceURL='https://other.example/'),
    lambda r:r['data']['metadata'].update(url='http://127.0.0.1/'),
    lambda r:r['data']['metadata'].update(contentType='application/pdf'),
    lambda r:r['data']['metadata'].update(numPages=2),
    lambda r:r['data'].update(rawHtml=''),
    lambda r:r['data'].update(rawHtml='x'*1_000_001),
])
def test_rejects_malicious_unbound_empty_or_extra_page_response(change):
    response=page(); change(response)
    f,calls=retriever(response)
    # Simulate actual public-DNS rejection of a provider-reported private host.
    f.direct.resolver=lambda h:['127.0.0.1'] if h=='127.0.0.1' else ['93.184.216.34']
    assert f.fetch(URL).content is None and len(calls)==1 and f.attempts[0]['outcome']=='rejected'


def test_ssrf_pdf_and_budget_blocked_never_fallback():
    f,calls=retriever(); f.direct.resolver=lambda h:['10.0.0.1']
    with pytest.raises(RetrievalBlocked): f.fetch(URL)
    assert not calls
    f,calls=retriever(); f.fetch('https://agency.example/a.pdf'); assert not calls
    f.budget.blocked=True
    with pytest.raises(BudgetExceeded): f.fetch(URL)
    assert not calls


def test_confirmed_ended_timeout_keeps_count_and_allows_next_document():
    f,calls=retriever()
    from dochunt.firecrawl import FirecrawlHTTPTransport
    class Ended(FirecrawlHTTPTransport):
        def __call__(self,*args):
            calls.append(args)
            if len(calls)==1: raise RetrievalTimeout()
            return page()
    f.transport=Ended()
    assert f.fetch(URL).content is None and not f.budget.blocked
    assert f.fetch(URL).content and len(f.attempts)==2


def test_unknown_callback_timeout_blocks_budget_without_retry():
    f,calls=retriever(); f.settings.firecrawl_timeout_seconds=.01
    f.transport=lambda *a:time.sleep(.1)
    # Initial remaining timeout shorter than API minimum deliberately skips dispatch.
    assert not f.fetch(URL).content and not f.attempts
    f.settings.firecrawl_timeout_seconds=1
    f.transport=lambda *a:(_ for _ in ()).throw(TimeoutError())
    with pytest.raises(BudgetExceeded,match='uncertain'): f.fetch(URL)
    assert f.budget.blocked and len(f.attempts)==1


def test_markdown_fallback_is_transformed_source_text():
    response=page(); response['data'].pop('rawHtml'); response['data']['markdown']='## Official filing\nSubmit a request.'
    f,_=retriever(response); r=f.fetch(URL)
    assert r.format=='markdown' and r.provenance['hash_basis']=='firecrawl.markdown.utf8'
    validate_provenance(asdict(r))


def test_config_setup_roundtrip_secret_names_and_validation(tmp_path):
    from dochunt.github_setup import serialize_config
    from dochunt.setup_server import form_config
    c=load_config(Path('examples/education.toml')); data=asdict(c)
    data['retrieval']={'firecrawl_enabled':True,'firecrawl_max_calls':6,'firecrawl_timeout_seconds':20}
    updated=form_config(data,c)
    path=tmp_path/'newsroom.toml'; path.write_text(serialize_config(updated))
    restored=load_config(path)
    assert restored.retrieval==Retrieval(True,6,20) and restored.secret_names['retrieval']=='FIRECRAWL_API_KEY'
    assert 'mock-firecrawl-key' not in path.read_text()
    for key,value in [('firecrawl_enabled','true'),('firecrawl_max_calls',21),('firecrawl_timeout_seconds',61),('firecrawl_api_key','secret')]:
        bad=asdict(c); bad['retrieval'][key]=value
        with pytest.raises(ValueError): config_from_data(bad)


def test_default_transport_timeout_kills_worker_and_exposes_no_key(monkeypatch):
    import subprocess
    from dochunt.firecrawl import FirecrawlHTTPTransport
    captured=[]
    def run(command,**kwargs):
        captured.append((command,kwargs))
        raise subprocess.TimeoutExpired(command,kwargs['timeout'])
    monkeypatch.setattr(subprocess,'run',run)
    with pytest.raises(RetrievalTimeout) as exc:
        FirecrawlHTTPTransport()(ENDPOINT,{'Authorization':'Bearer sensitive-mock'}, {'url':URL}, .1,100)
    assert 'sensitive-mock' not in str(exc.value) and 'sensitive-mock' not in str(captured[0][0])
    assert captured[0][1]['timeout']==.1 and b'sensitive-mock' in captured[0][1]['input']


def test_timeout_clamped_to_original_deadline():
    f,calls=retriever(); f.budget.started-=1498
    assert f.fetch(URL).content
    assert 1<=calls[0][3]<=2 and calls[0][2]['timeout']<=2000


def test_real_client_timeout_terminates_and_reaps_child(tmp_path,monkeypatch):
    import os
    import subprocess
    import sys
    from dochunt.firecrawl import FirecrawlHTTPTransport
    pidfile=tmp_path/'worker.pid'
    script='import os,time; from pathlib import Path; Path('+repr(str(pidfile))+').write_text(str(os.getpid())); time.sleep(60)'
    run=subprocess.run
    monkeypatch.setattr(subprocess,'run',lambda command,**kwargs:run([sys.executable,'-c',script],**kwargs))
    with pytest.raises(RetrievalTimeout):
        FirecrawlHTTPTransport()(ENDPOINT,{'Authorization':'Bearer synthetic'}, {'url':URL}, .5,100)
    pid=int(pidfile.read_text())
    with pytest.raises(ProcessLookupError): os.kill(pid,0)


@pytest.mark.parametrize('status,mime,body,expected_format',[
    (200,'application/pdf; charset=binary',b'%PDF-1.7\ninvalid public download', 'pdf'),
    (200,'application/octet-stream',b'%PDF-1.7\ninvalid public download', 'pdf'),
    (403,'application/pdf',b'', ''),
    (200,'image/png',b'\x89PNG\r\n\x1a\n', ''),
    (403,'application/zip',b'', ''),
])
def test_extensionless_pdf_and_known_unsupported_mime_never_scrape(status,mime,body,expected_format):
    f,calls=retriever()
    f.direct.connector=lambda *a:FetchResponse(status,{'content-type':mime},body)
    result=f.fetch(URL)
    assert result.content is None
    assert result.content_type==mime.split(';')[0]
    assert result.format==expected_format
    assert not calls and not f.attempts and f.budget.usage.fetches==1
