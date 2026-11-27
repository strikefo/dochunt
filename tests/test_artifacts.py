from pathlib import Path
import pytest
from datetime import date
from support import config, FakeAgent, no_fetch
from dochunt.hunt import preview as hunt_preview
from dochunt.artifacts import load_preview, validate_run, validate_selection

D=date(2026,10,2)


def preview(c,path,*,synthetic=False,agent=None):
    return hunt_preview(c,path,local_date=D,synthetic=synthetic,agent=agent or FakeAgent(),fetch=no_fetch)


def test_safe_input():
    for value in ['$(curl evil)', '1; echo evil', '../1', '0', '1\n2']:
        with pytest.raises(ValueError): validate_selection(value)

def test_wrong_repo_branch_or_artifact():
    run={'id':12,'repository':{'full_name':'news/own','private':True},'head_repository':{'full_name':'news/own'},'head_branch':'main','path':'.github/workflows/document-hunt.yml','event':'workflow_dispatch','status':'completed','conclusion':'success'}
    validate_run(run,'news/own','main',12)
    validate_run(dict(run,path='.github/workflows/document-hunt.yml@main'),'news/own','main',12)
    for key,value in [('head_branch','evil'),('repository',{'full_name':'other/repo','private':True}),('path','other.yml'),('event','pull_request')]:
        with pytest.raises(ValueError): validate_run(dict(run,**{key:value}),'news/own','main',12)

def test_wrong_artifact_and_config(tmp_path):
    c=config(); preview(c,tmp_path,synthetic=True)
    with pytest.raises(ValueError): load_preview(tmp_path,c,require_paid=True)
    (tmp_path/'extra').write_text('x')
    with pytest.raises(ValueError): load_preview(tmp_path,c)

def test_config_payload_consistency(tmp_path):
    c=config(); preview(c,tmp_path)
    load_preview(tmp_path,c,require_paid=True)
    c.email.recipients=['different@example.invalid']
    with pytest.raises(ValueError): load_preview(tmp_path,c,require_paid=True)

def test_artifact_redirect_removes_token():
    import urllib.request
    from dochunt.artifacts import ArtifactRedirect
    request=urllib.request.Request('https://api.github.com/artifact',headers={'Authorization':'Bearer private'})
    redirected=ArtifactRedirect().redirect_request(request,None,302,'',{},'https://blob.example.org/zip')
    assert redirected.get_header('Authorization') is None

def test_wrong_artifact_name_rejected_before_download(tmp_path):
    import json
    from dochunt.artifacts import download_preview
    run={'id':12,'repository':{'full_name':'news/own','private':True},'head_repository':{'full_name':'news/own'},'head_branch':'main','path':'.github/workflows/document-hunt.yml','event':'workflow_dispatch','status':'completed','conclusion':'success'}
    calls=[]
    class Response:
        def __init__(self,data): self.data=data
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def read(self,n): return json.dumps(self.data).encode()
    def opener(request,**kwargs):
        calls.append(request.full_url)
        return Response(run if len(calls)==1 else {'artifacts':[{'id':1,'name':'document-hunt-12','expired':False}]})
    with pytest.raises(ValueError): download_preview('12','news/own','main',tmp_path/'selected',token='fixture',opener=opener)
    assert len(calls)==2 and not (tmp_path/'selected').exists()

def test_changed_payload_hash_and_render_rejected(tmp_path):
    import json
    from dochunt.artifacts import write_manifest
    c=config(); preview(c,tmp_path)
    (tmp_path/'preview.html').write_text('tampered')
    with pytest.raises(ValueError): load_preview(tmp_path,c)
    write_manifest(tmp_path,c,False)
    with pytest.raises(ValueError): load_preview(tmp_path,c)

def zip_opener(names,*,oversize=False):
    import io, json, zipfile
    from dochunt.artifacts import FILES
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for name in names: archive.writestr(name,b'x'*10_000_001 if oversize and name=='issue.json' else b'fixture')
    run={'id':12,'repository':{'full_name':'news/own','private':True},'head_repository':{'full_name':'news/own'},'head_branch':'main','path':'.github/workflows/document-hunt.yml','event':'workflow_dispatch','status':'completed','conclusion':'success'}
    class Response:
        def __init__(self,data): self.data=data
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def read(self,n): return self.data[:n]
    def opener(request,**kwargs):
        url=request.full_url
        if url.endswith('/zip'): return Response(buffer.getvalue())
        data={'artifacts':[{'id':1,'name':'paid-ai-preview-12','expired':False}]} if '/artifacts?' in url else run
        return Response(json.dumps(data).encode())
    return opener

def test_valid_zip_exact_file_extraction(tmp_path):
    from dochunt.artifacts import download_preview, FILES
    out=tmp_path/'selected'; download_preview('12','news/own','main',out,token='fixture',opener=zip_opener(FILES))
    assert {p.name for p in out.iterdir()}==FILES and all(p.read_bytes()==b'fixture' for p in out.iterdir())

@pytest.mark.parametrize('names,oversize',[
    (['../outside','preview.html','preview.txt','preview-manifest.json'],False),
    (['issue.json','issue.json','preview.txt','preview-manifest.json'],False),
    (['issue.json','preview.html','preview.txt','preview-manifest.json'],True),
])
def test_zip_traversal_duplicate_and_expanded_size_rejected(tmp_path,names,oversize):
    from dochunt.artifacts import download_preview
    with pytest.raises(ValueError): download_preview('12','news/own','main',tmp_path/'selected',token='fixture',opener=zip_opener(names,oversize=oversize))
    assert not (tmp_path/'selected').exists() and not (tmp_path/'outside').exists()


def test_branding_change_invalidates_preview_contract(tmp_path):
    c=config(); preview(c,tmp_path,synthetic=True)
    c.identity.sender_name='Changed newsroom'
    with pytest.raises(ValueError): load_preview(tmp_path,c)


def test_old_renderer_preview_is_rejected_without_rewriting_saved_body(tmp_path, monkeypatch):
    """A selected old preview cannot silently become a different new email."""
    from dochunt.artifacts import write_manifest
    c = config(); preview(c, tmp_path, synthetic=True)
    saved_html = (tmp_path/'preview.html').read_text()
    # Emulate an intact artifact produced by the old renderer.
    old_html = saved_html + '<p>Newsworthiness: 8/10</p>'
    (tmp_path/'preview.html').write_text(old_html)
    write_manifest(tmp_path, c, True)
    with pytest.raises(ValueError, match='Payload differs'):
        load_preview(tmp_path, c)
    assert (tmp_path/'preview.html').read_text() == old_html


def test_raw_agent_outputs_are_kept_out_of_the_send_contract(tmp_path):
    c=config(); preview(c,tmp_path)
    assert (tmp_path/'raw'/'prompt.md').exists() and (tmp_path/'raw'/'agent-output-1.json').exists()
    issue,_=load_preview(tmp_path,c,require_paid=True)
    assert len(issue.leads)==3
    (tmp_path/'issue.json').write_text((tmp_path/'issue.json').read_text().replace('road-repair','bridge-repair'))
    with pytest.raises(ValueError): load_preview(tmp_path,c)


@pytest.mark.parametrize('command,extra', [
    ('deliver', []),
    ('resend', ['--attempt-id', 'extra-copy']),
    ('preview-test', ['--attempt-id', 'test-1', '--recipient', 'reporter@example.org']),
])
def test_standalone_sends_refuse_a_synthetic_preview(tmp_path, capsys, monkeypatch, command, extra):
    """A fictional preview is never sendable, to the full list or to one test address."""
    from dochunt.cli import main
    import dochunt.delivery as delivery
    monkeypatch.setattr(delivery, 'send', lambda *a, **k: pytest.fail('a synthetic preview reached send'))
    cfg = 'examples/education.toml'
    main(['preview', '--synthetic', '--config', cfg, '--output-dir', str(tmp_path / 'out')])
    with pytest.raises(SystemExit) as stop:
        main([command, '--config', cfg, '--issue-dir', str(tmp_path / 'out'), '--state-dir', str(tmp_path / 'state'),
              '--repo', str(tmp_path), *extra])
    assert stop.value.code == 2 and 'Preview/config mismatch' in capsys.readouterr().err


@pytest.mark.parametrize('command,extra,repeat', [
    ('deliver', [], False),
    ('resend', ['--attempt-id', 'extra-copy'], True),
    ('preview-test', ['--attempt-id', 'test-1', '--recipient', 'reporter@example.org'], False),
])
def test_standalone_sends_pass_the_resend_and_saved_email_rules(tmp_path, monkeypatch, command, extra, repeat):
    """The CLI wiring: only resend may repeat a sent issue, and every standalone retry must match the saved email."""
    from dochunt.cli import main
    from dochunt.models import SendReceipt
    import dochunt.delivery as delivery
    import dochunt.state as state
    preview(config(), tmp_path / 'out')   # a paid preview from the fake agent
    calls = []
    monkeypatch.setattr(state, 'PublishedStateStore', lambda *a, **k: object())
    monkeypatch.setattr(delivery, 'send', lambda prepared, store, **k: calls.append(k) or SendReceipt('resend', 'accepted', provider_id='fake', accepted_at='2026-10-02T12:00:00+00:00'))
    main([command, '--config', 'examples/education.toml', '--issue-dir', str(tmp_path / 'out'),
          '--state-dir', str(tmp_path / 'state'), '--repo', str(tmp_path), *extra])
    kwargs, = calls
    assert kwargs['repeat'] is repeat and kwargs['match_saved'] is True
