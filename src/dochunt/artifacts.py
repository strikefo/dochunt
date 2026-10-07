"""Trusted previous-run preview selection; no research or delivery credentials."""
from dataclasses import asdict
from datetime import date
import hashlib
import io
import json
import os
from pathlib import Path
import re
import urllib.request
import zipfile
from urllib.parse import urlsplit

class ArtifactRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        if urlsplit(newurl).scheme != "https": raise ValueError("Unsafe artifact redirect")
        redirected=super().redirect_request(req,fp,code,msg,headers,newurl)
        if urlsplit(newurl).netloc != "api.github.com": redirected.remove_header("Authorization")
        return redirected
from .models import Issue, Lead, Evidence, Usage
from .render import render_issue, RenderedIssue

# Preview artifacts; raw model outputs sit in an optional raw/ directory that delivery never reads.
FILES={'issue.json','preview.html','preview.txt','preview-manifest.json','engine-result.json','sources.json','analysed-documents.json'}
ENGINE_FILES=FILES

def config_digest(config):
    return hashlib.sha256(json.dumps(asdict(config),sort_keys=True).encode()).hexdigest()

def write_manifest(path,config,synthetic,files=FILES):
    path=Path(path)
    manifest={'version':1,'mode':'synthetic-preview' if synthetic else 'paid-ai-preview','config_sha256':config_digest(config),'files':{name:hashlib.sha256((path/name).read_bytes()).hexdigest() for name in files-{'preview-manifest.json'}}}
    (path/'preview-manifest.json').write_text(json.dumps(manifest,sort_keys=True)+'\n')

def load_preview(path,config,*,require_paid=False,require_send=False):
    path=Path(path)
    names={p.name for p in path.iterdir()}
    if 'raw' in names and ((path/'raw').is_symlink() or not (path/'raw').is_dir()): raise ValueError('Unexpected preview files')
    names.discard('raw')
    files=FILES
    if names!=files or any(not (path/name).is_file() or (path/name).is_symlink() for name in files): raise ValueError('Unexpected preview files')
    manifest=json.loads((path/'preview-manifest.json').read_text())
    if manifest.get('version')!=1 or manifest.get('mode') not in {'synthetic-preview','paid-ai-preview'} or (require_paid and manifest['mode']!='paid-ai-preview') or manifest.get('config_sha256')!=config_digest(config): raise ValueError('Preview/config mismatch')
    if manifest.get('files')!={name:hashlib.sha256((path/name).read_bytes()).hexdigest() for name in files-{'preview-manifest.json'}}: raise ValueError('Preview hashes differ')
    data=json.loads((path/'issue.json').read_text()); fingerprint=data.pop('fingerprint')
    data['local_date']=date.fromisoformat(data['local_date']); data['leads']=[Lead(**v) for v in data['leads']]; data['evidence']=[Evidence(**v) for v in data['evidence']]; data['usage']=Usage(**data['usage'])
    issue=Issue(**data)
    # Every evidence excerpt must be literal text of a saved source with the same hash.
    from .engine import validate_engine_artifacts
    validate_engine_artifacts(issue,json.loads((path/'sources.json').read_text()))
    rendered=RenderedIssue(issue.subject,(path/'preview.html').read_text(),(path/'preview.txt').read_text(),fingerprint)
    if render_issue(issue,config)!=rendered: raise ValueError('Payload differs from finalized issue')
    return issue,rendered

def validate_selection(value):
    if not isinstance(value,str) or not re.fullmatch(r'[1-9][0-9]{0,19}',value): raise ValueError('Select numeric previous run ID')
    return int(value)

def validate_run(run,repo,branch,run_id):
    if run.get('id')!=run_id or run.get('repository',{}).get('full_name')!=repo or run.get('repository',{}).get('private') is not True or run.get('head_repository',{}).get('full_name')!=repo or run.get('head_branch')!=branch or run.get('path') not in {'.github/workflows/document-hunt.yml',f'.github/workflows/document-hunt.yml@{branch}',f'.github/workflows/document-hunt.yml@refs/heads/{branch}'} or run.get('event')!='workflow_dispatch' or run.get('status')!='completed' or run.get('conclusion')!='success': raise ValueError('Untrusted preview run')

def download_preview(run_id,repo,branch,output,*,token=None,opener=None):
    run_id=validate_selection(run_id)
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo): raise ValueError('Invalid repository')
    if str(run_id)==os.environ.get('GITHUB_RUN_ID'): raise ValueError('Select a previous completed run')
    token=token or os.environ.get('GH_TOKEN'); opener=opener or urllib.request.build_opener(ArtifactRedirect()).open
    if not token: raise ValueError('Missing Actions read token')
    def request(path,raw=False):
        req=urllib.request.Request('https://api.github.com'+path,headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'})
        with opener(req,timeout=30) as response:
            body=response.read(10_000_001)
        if len(body)>10_000_000: raise ValueError('Artifact too large')
        return body if raw else json.loads(body)
    run=request(f'/repos/{repo}/actions/runs/{run_id}'); validate_run(run,repo,branch,run_id)
    artifacts=request(f'/repos/{repo}/actions/runs/{run_id}/artifacts?per_page=100')['artifacts']
    matches=[a for a in artifacts if a.get('name')==f'paid-ai-preview-{run_id}' and not a.get('expired')]
    if len(matches)!=1 or type(matches[0].get('id')) is not int: raise ValueError('Select a paid preview artifact')
    archive=request(f'/repos/{repo}/actions/artifacts/{matches[0]["id"]}/zip',True)
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        entries=zipped.infolist()
        if len(entries)!=len(FILES) or {e.filename for e in entries}!=FILES or sum(e.file_size for e in entries)>10_000_000: raise ValueError('Unexpected archive files')
        output=Path(output); output.mkdir(parents=True,exist_ok=False)
        for entry in entries: (output/entry.filename).write_bytes(zipped.read(entry))
