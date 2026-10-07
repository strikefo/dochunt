from pathlib import Path
import pytest
from datetime import date
from support import config, provider as fictional_provider
from dochunt.cli import preview
from dochunt.artifacts import load_preview, validate_run, validate_selection

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
    c=config(); preview(c,tmp_path,provider=fictional_provider(c),local_date=date(2026,10,2))
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
    c=config(); preview(c,tmp_path,provider=fictional_provider(c),local_date=date(2026,10,2))
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
    from support import config
    from dochunt.cli import preview
    from dochunt.artifacts import load_preview
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
