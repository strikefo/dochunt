import pytest
from dochunt.retrieval import PublicRetriever, RetrievalBlocked, FetchResponse
from dochunt.budget import Budget
from dochunt.models import Limits


def test_private_destination_redirect_is_blocked():
    calls = []
    def connect(url, ip, timeout, cap):
        calls.append(url)
        return FetchResponse(302, {'location':'http://127.0.0.1/secret'}, b'')
    r = PublicRetriever(Budget(Limits(), lambda: 0), resolver=lambda h:['93.184.216.34'] if h == 'example.org' else ['127.0.0.1'], connector=connect)
    with pytest.raises(RetrievalBlocked): r.fetch('https://example.org/a')
    assert len(calls) == 1


@pytest.mark.parametrize('url', ['http://localhost/x','https://user:pw@example.org','https://example.org:8443','file:///a','http://169.254.169.254/'])
def test_private_or_credential_destinations_blocked(url):
    r = PublicRetriever(Budget(Limits(), lambda:0), resolver=lambda h:['127.0.0.1'])
    with pytest.raises(RetrievalBlocked): r.fetch(url)


def test_dns_pin_size_and_inaccessible_coverage():
    calls=[]
    def connect(url,ip,timeout,cap):
        calls.append(ip)
        return FetchResponse(403, {}, b'forbidden')
    r=PublicRetriever(Budget(Limits(), lambda:0),resolver=lambda h:['93.184.216.34'], connector=connect)
    result=r.fetch('https://example.org/a')
    assert calls == ['93.184.216.34']
    assert '403' in result.coverage and result.content is None


def test_oversize_and_mixed_dns_blocked():
    b=Budget(Limits(),lambda:0)
    r=PublicRetriever(b,resolver=lambda h:['93.184.216.34','10.0.0.1'])
    with pytest.raises(RetrievalBlocked): r.fetch('https://example.org')
    r=PublicRetriever(b,resolver=lambda h:['93.184.216.34'],connector=lambda *a:FetchResponse(200,{'content-type':'text/plain'},b'x'*11),max_bytes=10)
    with pytest.raises(RetrievalBlocked): r.fetch('https://example.org')


def text_pdf(text=None):
    """A tiny hand-generated offline PDF with a page/content stream."""
    content=('BT /F1 12 Tf 20 50 Td ('+text+') Tj ET').encode() if text else b''
    objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 100] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',b'<< /Length '+str(len(content)).encode()+b' >>\nstream\n'+content+b'\nendstream']
    body=b'%PDF-1.4\n'; offsets=[]
    for i,obj in enumerate(objects,1):
        offsets.append(len(body)); body+=str(i).encode()+b' 0 obj\n'+obj+b'\nendobj\n'
    xref=len(body); body+=b'xref\n0 6\n0000000000 65535 f \n'+b''.join(f'{o:010} 00000 n \n'.encode() for o in offsets)
    return body+b'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n'+str(xref).encode()+b'\n%%EOF\n'


@pytest.mark.parametrize('body,expected',[(text_pdf('Verified audit passage'),True),(text_pdf(),False),(b'not a PDF',False)])
def test_pdf_extracts_real_page_text_or_reports_scan_invalid(body,expected):
    r=PublicRetriever(Budget(Limits()),resolver=lambda _:['93.184.216.34'],connector=lambda *a:FetchResponse(200,{'content-type':'application/pdf'},body))
    result=r.fetch('https://example.org/report.pdf')
    assert bool(result.content)==expected
    if expected:
        assert result.sections==[{'locator':'PDF page 1','text':'Verified audit passage'}]
        assert len(result.content_hash)==64 and result.format=='pdf'
    else: assert 'scanned' in result.coverage


def test_html_scripts_are_not_source_evidence():
    r=PublicRetriever(Budget(Limits()),resolver=lambda _:['93.184.216.34'],connector=lambda *a:FetchResponse(200,{'content-type':'text/html'},b'<title>Audit</title><script>fabricated finding</script><p>Actual passage</p><a href="/portal">Request form</a>'))
    result=r.fetch('https://example.org/report')
    assert 'fabricated' not in result.content and 'Actual passage' in result.content
    assert result.links==['https://example.org/portal']


@pytest.fixture
def isolated_fixture_runner(tmp_path):
    """Scripted public transport uses the same kill/wait path as real DNS/HTTP."""
    import sys
    from functools import partial
    from dochunt.retrieval import isolated_operation
    script=tmp_path/'public-worker.py'; pidfile=tmp_path/'worker.pid'; latefile=tmp_path/'late-completion'
    script.write_text('''import base64,json,os,sys,time
from pathlib import Path
job=json.load(sys.stdin); op=job['operation']; args=job['args']
slow=(op=='resolve' and args[0]=='slow-dns.example.org') or (op=='connect' and args[0].endswith('/slow'))
if slow:
    Path('''+repr(str(pidfile))+''').write_text(str(os.getpid()))
    time.sleep(10)
    Path('''+repr(str(latefile))+''').write_text('uncontrolled completion')
if op=='resolve':
    result=['127.0.0.1'] if args[0]=='127.0.0.1' else ['93.184.216.34']
else:
    result={'status':302,'headers':{'location':'http://127.0.0.1/private'},'body':''} if args[0].endswith('/redirect') else {'status':200,'headers':{'content-type':'text/plain'},'body':base64.b64encode(b'Actual public document').decode()}
print(json.dumps({'result':result}))
''')
    return partial(isolated_operation,command=[sys.executable,str(script)]),pidfile,latefile


@pytest.mark.parametrize('url,expected_fetches',[('https://example.org/slow',2),('https://slow-dns.example.org/report',1)])
def test_timed_out_worker_is_reaped_and_next_document_remains_available(isolated_fixture_runner,monkeypatch,url,expected_fetches):
    import os
    from time import monotonic
    runner,pidfile,latefile=isolated_fixture_runner
    # Long enough for a fresh interpreter to start under a loaded test run, far short of the 10s sleep.
    monkeypatch.setattr('dochunt.retrieval.DNS_TIMEOUT_SECONDS',1.5)
    monkeypatch.setattr('dochunt.retrieval.HTTP_TIMEOUT_SECONDS',1.5)
    b=Budget(Limits()); r=PublicRetriever(b,operation_runner=runner)
    started=monotonic(); result=r.fetch(url)
    assert result.content is None and 'deadline' in result.coverage
    assert not b.blocked and monotonic()-started<5
    with pytest.raises(ProcessLookupError): os.kill(int(pidfile.read_text()),0)
    assert not latefile.exists()
    next_document=r.fetch('https://example.org/fast')
    assert next_document.content=='Actual public document' and not b.blocked
    assert b.usage.fetches==expected_fetches


def test_isolated_redirect_cannot_reach_private_address(isolated_fixture_runner):
    runner,_,_=isolated_fixture_runner; b=Budget(Limits()); r=PublicRetriever(b,operation_runner=runner)
    with pytest.raises(RetrievalBlocked): r.fetch('https://example.org/redirect')
    assert b.usage.fetches==1 and not b.blocked


def test_killed_source_worker_does_not_reset_global_deadline(isolated_fixture_runner):
    from dochunt.budget import BudgetExceeded
    from dochunt.retrieval import RetrievalTimeout
    runner,_,_=isolated_fixture_runner; now=[0]
    b=Budget(Limits(),lambda:now[0]); now[0]=1499.95
    observed=[]
    def bounded_runner(operation,args,timeout):
        observed.append(timeout)
        try: return runner(operation,args,timeout)
        except RetrievalTimeout:
            now[0]=1500.1
            raise
    r=PublicRetriever(b,operation_runner=bounded_runner)
    with pytest.raises(BudgetExceeded): r.fetch('https://slow-dns.example.org/report')
    assert observed[0]==pytest.approx(0.05) and b.started==0 and b.usage.fetches==0
    with pytest.raises(BudgetExceeded): r.fetch('https://example.org/fast')


def test_default_worker_resolves_local_numeric_ip_but_parent_blocks_fetch():
    # Actual subprocess/module/DNS result, with no outbound HTTP request.
    b=Budget(Limits()); r=PublicRetriever(b)
    with pytest.raises(RetrievalBlocked): r.fetch('http://127.0.0.1/private')
    assert b.usage.fetches==0 and not b.blocked


def test_uncancellable_injected_resolver_timeout_still_blocks_budget(monkeypatch):
    import threading
    from dochunt.budget import BudgetExceeded
    release=threading.Event(); b=Budget(Limits())
    monkeypatch.setattr('dochunt.retrieval.DNS_TIMEOUT_SECONDS',0.02)
    r=PublicRetriever(b,resolver=lambda host:release.wait())
    try:
        with pytest.raises(RetrievalBlocked): r.fetch('https://example.org/report')
        assert b.blocked and b.usage.fetches==0
        with pytest.raises(BudgetExceeded): r.fetch('https://example.org/next')
    finally: release.set()


def test_finished_isolated_socket_timeout_is_a_source_limitation(tmp_path):
    from functools import partial
    import sys
    from dochunt.retrieval import isolated_operation
    script=tmp_path/'socket-timeout.py'
    script.write_text("import json;print(json.dumps({'error':'timeout'}))")
    runner=partial(isolated_operation,command=[sys.executable,str(script)])
    b=Budget(Limits()); r=PublicRetriever(b,resolver=lambda _:['93.184.216.34'],operation_runner=runner)
    result=r.fetch('https://example.org/document')
    assert result.content is None and 'HTTP worker' in result.coverage and not b.blocked
    assert b.usage.fetches==1


def test_resolver_returns_deterministic_ipv4_then_ipv6(monkeypatch):
    from dochunt.retrieval import resolve
    records=[(None,None,None,None,(ip,0)) for ip in ['2606:4700::1111','93.184.216.35','93.184.216.34','93.184.216.35']]
    monkeypatch.setattr('dochunt.retrieval.socket.getaddrinfo',lambda *a,**k:records)
    assert resolve('example.org')==['93.184.216.34','93.184.216.35','2606:4700::1111']


def test_no_ipv6_route_does_not_hide_accessible_ipv4_document():
    calls=[]; b=Budget(Limits(),lambda:0)
    def connector(url,ip,timeout,cap):
        calls.append(ip)
        if ':' in ip: raise OSError(65,'No route to host')
        return FetchResponse(200,{'content-type':'text/plain'},b'Actual public document')
    r=PublicRetriever(b,resolver=lambda _:['2606:4700::1111','93.184.216.35','93.184.216.34'],connector=connector)
    assert r.fetch('https://example.org/document').content=='Actual public document'
    assert calls==['93.184.216.34'] and b.usage.fetches==1


def test_other_public_address_family_is_a_bounded_counted_fallback():
    calls=[]; b=Budget(Limits(),lambda:0)
    def connector(url,ip,timeout,cap):
        calls.append(ip)
        if ':' not in ip: raise OSError('IPv4 route unavailable')
        return FetchResponse(200,{'content-type':'text/plain'},b'Actual IPv6 document')
    r=PublicRetriever(b,resolver=lambda _:['93.184.216.35','2606:4700::1112','93.184.216.34','2606:4700::1111'],connector=connector)
    assert r.fetch('https://example.org/document').content=='Actual IPv6 document'
    assert calls==['93.184.216.34','2606:4700::1111'] and b.usage.fetches==2


def test_failed_families_do_not_try_every_address():
    calls=[]; b=Budget(Limits(),lambda:0)
    def connector(url,ip,*args):
        calls.append(ip); raise OSError('No route')
    r=PublicRetriever(b,resolver=lambda _:['93.184.216.35','2606:4700::1112','93.184.216.34','2606:4700::1111'],connector=connector)
    result=r.fetch('https://example.org/document')
    assert result.content is None and 'network' in result.coverage
    assert calls==['93.184.216.34','2606:4700::1111'] and b.usage.fetches==2 and not b.blocked


def test_real_http_failure_does_not_retry_address_families():
    calls=[]; b=Budget(Limits(),lambda:0)
    def connector(url,ip,*args):
        calls.append(ip); return FetchResponse(403,{},b'Forbidden')
    r=PublicRetriever(b,resolver=lambda _:['2606:4700::1111','93.184.216.34'],connector=connector)
    assert '403' in r.fetch('https://example.org/document').coverage
    assert calls==['93.184.216.34'] and b.usage.fetches==1


def test_alternate_address_never_bypasses_validation_of_all_dns_results():
    calls=[]; b=Budget(Limits(),lambda:0)
    r=PublicRetriever(b,resolver=lambda _:['93.184.216.34','2606:4700::1111','fd00::1'],connector=lambda *a:calls.append(a))
    with pytest.raises(RetrievalBlocked): r.fetch('https://example.org/document')
    assert calls==[] and b.usage.fetches==0


def test_address_fallback_shares_original_http_and_application_deadline():
    now=[0]; b=Budget(Limits(),lambda:now[0]); now[0]=1490
    calls=[]
    def connector(url,ip,timeout,cap):
        calls.append((ip,timeout))
        if ':' not in ip:
            now[0]=1496; raise OSError('No route')
        return FetchResponse(200,{'content-type':'text/plain'},b'Actual document')
    r=PublicRetriever(b,resolver=lambda _:['93.184.216.34','2606:4700::1111'],connector=connector)
    assert r.fetch('https://example.org/document').content=='Actual document'
    assert calls==[('93.184.216.34',10),('2606:4700::1111',4)]
    assert b.started==0 and b.usage.fetches==2


@pytest.mark.parametrize('reason',['fetches','deadline'])
def test_fallback_stops_at_shared_budget_boundary(reason):
    from dochunt.budget import BudgetExceeded
    now=[0]; b=Budget(Limits(fetches=1 if reason=='fetches' else 30),lambda:now[0]); calls=[]
    def connector(url,ip,*args):
        calls.append(ip)
        if reason=='deadline': now[0]=1501
        raise OSError('No route')
    r=PublicRetriever(b,resolver=lambda _:['93.184.216.34','2606:4700::1111'],connector=connector)
    with pytest.raises(BudgetExceeded): r.fetch('https://example.org/document')
    assert calls==['93.184.216.34'] and b.usage.fetches==1 and b.started==0


def test_confirmed_ended_timeout_can_fallback_with_only_remaining_http_time():
    from dochunt.retrieval import RetrievalTimeout
    now=[0]; b=Budget(Limits(),lambda:now[0]); calls=[]
    def runner(operation,args,timeout):
        calls.append((args[1],timeout))
        if ':' not in args[1]:
            now[0]=6; raise RetrievalTimeout('Socket ended')
        return FetchResponse(200,{'content-type':'text/plain'},b'Actual document')
    r=PublicRetriever(b,resolver=lambda _:['93.184.216.34','2606:4700::1111'],operation_runner=runner)
    assert r.fetch('https://example.org/document').content=='Actual document'
    assert calls==[('93.184.216.34',15),('2606:4700::1111',9)] and b.usage.fetches==2 and not b.blocked


def test_connect_encodes_literal_spaces_without_changing_authority_or_escapes(monkeypatch):
    from dochunt.retrieval import connect
    requests=[]
    class Response:
        status=200
        def getheaders(self): return [('Content-Type','text/plain')]
        def read(self,cap): return b'Actual document'
    class Connection:
        def __init__(self,host,port,timeout): assert (host,port)==('example.org',80)
        def request(self,*args,**kwargs): requests.append(args)
        def getresponse(self): return Response()
        def close(self): pass
    monkeypatch.setattr('dochunt.retrieval.http.client.HTTPConnection',Connection)
    monkeypatch.setattr('dochunt.retrieval.socket.create_connection',lambda *a,**k:object())
    result=connect('http://example.org/Reports/Final report%20A.pdf?search=labor audit&year=2026','93.184.216.34',1,1000)
    assert result.status==200
    assert requests==[('GET','/Reports/Final%20report%20A.pdf?search=labor%20audit&year=2026')]


def test_isolated_network_failure_uses_validated_family_fallback(tmp_path):
    from functools import partial
    import sys
    from dochunt.retrieval import isolated_operation
    script=tmp_path/'family-worker.py'; attempts=tmp_path/'attempts'
    script.write_text('''import base64,json,sys
from pathlib import Path
job=json.load(sys.stdin); args=job['args']
if job['operation']=='resolve': result={'result':['2606:4700::1111','93.184.216.34']}
else:
    with Path('''+repr(str(attempts))+''').open('a') as f: f.write(args[1]+'\\n')
    result={'error':'unavailable'} if ':' not in args[1] else {'result':{'status':200,'headers':{'content-type':'text/plain'},'body':base64.b64encode(b'Actual alternate-family document').decode()}}
print(json.dumps(result))
''')
    b=Budget(Limits()); r=PublicRetriever(b,operation_runner=partial(isolated_operation,command=[sys.executable,str(script)]))
    assert r.fetch('https://example.org/document').content=='Actual alternate-family document'
    assert attempts.read_text().splitlines()==['93.184.216.34','2606:4700::1111']
    assert b.usage.fetches==2 and not b.blocked


def test_uncancellable_connector_timeout_never_launches_an_alternate(monkeypatch):
    import threading
    release=threading.Event(); calls=[]; b=Budget(Limits())
    monkeypatch.setattr('dochunt.retrieval.HTTP_TIMEOUT_SECONDS',0.02)
    def connector(url,ip,*args):
        calls.append(ip); release.wait()
    r=PublicRetriever(b,resolver=lambda _:['2606:4700::1111','93.184.216.34'],connector=connector)
    try:
        assert r.fetch('https://example.org/document').content is None
        assert b.blocked and b.usage.fetches==1 and calls==['93.184.216.34']
    finally: release.set()


@pytest.mark.parametrize('reading_password',['','required-password'])
def test_aes_pdf_only_opens_with_empty_reading_password(reading_password):
    import io
    from pypdf import PdfReader,PdfWriter
    writer=PdfWriter(); writer.append_pages_from_reader(PdfReader(io.BytesIO(text_pdf('Public audit finding'))))
    writer.encrypt(reading_password,owner_password='owner-only',algorithm='AES-256')
    stream=io.BytesIO(); writer.write(stream); body=stream.getvalue()
    r=PublicRetriever(Budget(Limits()),resolver=lambda _:['93.184.216.34'],connector=lambda *a:FetchResponse(200,{'content-type':'application/pdf'},body))
    result=r.fetch('https://example.org/audit.pdf')
    if reading_password:
        assert result.content is None and 'unsupported' in result.coverage
    else:
        assert result.sections==[{'locator':'PDF page 1','text':'Public audit finding'}]
        assert result.content_hash and result.format=='pdf'


@pytest.mark.parametrize('opening,closing',[('<main>','</main>'),('<div role="main">','</div>')])
def test_html_main_content_and_body_links_survive_navigation_boilerplate(opening,closing):
    from dochunt.extraction import extract
    menus=''.join('<a href="/menu/'+str(i)+'">Navigation menu '+str(i)+'</a>' for i in range(150))
    passage='Records Office accepts requests through the official filing portal.'
    body=('<title>Official instructions</title><header>'+menus+'</header><nav>Menu only</nav>'+opening+'<h1>How to request records</h1><p>'+passage+'</p><a href="/file-request">File request</a><script>invented route</script>'+closing+'<footer>Footer boilerplate</footer>').encode()
    doc=extract(body,'text/html','https://example.org/instructions',10)
    text=doc['sections'][0]['text']
    assert passage in text and 'Navigation menu' not in text and 'Menu only' not in text and 'Footer boilerplate' not in text and 'invented route' not in text
    assert doc['title']=='Official instructions' and doc['sections'][0]['locator']=='HTML main content'
    assert doc['links'][0]=='https://example.org/file-request'
    assert len(doc['links'])<=100


def test_html_without_main_keeps_document_body_and_links():
    from dochunt.extraction import extract
    body=b'<title>Records</title><nav><a href="/menu">Navigation</a></nav><p>Actual public record passage.</p><a href="/filing">Filing instructions</a>'
    doc=extract(body,'text/html','https://example.org/document',10)
    assert doc['sections']==[{'locator':'HTML text','text':'Actual public record passage. Filing instructions'}]
    assert doc['links']==['https://example.org/filing']


def test_empty_main_falls_back_to_visible_document_body():
    from dochunt.extraction import extract
    doc=extract(b'<main><script>Hidden claim</script></main><p>Visible actual source.</p>','text/html','https://example.org/source',10)
    assert doc['sections']==[{'locator':'HTML text','text':'Visible actual source.'}]


def test_html_stray_and_unclosed_navigation_markup_cannot_hide_actual_main():
    from dochunt.extraction import extract
    body=b'<nav><div>Navigation menu</nav></stray><main><p>Actual source passage.</p><a href="/records">Records</a></main>'
    doc=extract(body,'text/html','https://example.org/source',10)
    assert doc['sections']==[{'locator':'HTML main content','text':'Actual source passage. Records'}]
    assert doc['links']==['https://example.org/records']
