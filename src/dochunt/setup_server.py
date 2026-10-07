"""Temporary loopback-only setup. Config and credentials stay in memory until confirmation."""
from dataclasses import asdict
from copy import deepcopy
from http.server import HTTPServer, BaseHTTPRequestHandler
from importlib.resources import files
from pathlib import Path
import json
import secrets
import tomllib
import webbrowser
from .config import load_config, config_from_data, validate_model_configuration, ModelConfigurationError
from .github_setup import inspect_target, serialize_config, save_config, set_secrets, require_secrets, RepoTarget, SaveResult, SecretUpdateResult, verify_saved_target


def form_config(data,existing,*,enable=False):
    if not isinstance(data,dict) or set(data)!=set(asdict(existing)): raise ValueError('Submit all configuration sections')
    data=deepcopy(data)
    # Limits and every unedited setting are retained; callers cannot silently omit sections.
    if not isinstance(data.get('schedule'),dict): raise ValueError('Schedule must be a table')
    data['schedule']['enabled']=enable
    config=config_from_data(data)
    validate_model_configuration(config)
    return config

class GitHubAdapter:
    def __init__(self,path):
        self.path=Path(path); self.target=inspect_target(self.path); self.config=load_config(self.path/'newsroom.toml')
    def inspect(self):
        self.target=inspect_target(self.path); self.config=config_from_data(tomllib.loads(self.target.config_text)); return self.target
    def save(self,target,config):
        result=save_config(target,config)
        if result.pushed: self.config=config
        return result
    def refresh(self,target,config,result): return verify_saved_target(target,config,result.verified_head)
    def secrets(self,target,values): return set_secrets(target,values)

class MockAdapter:
    """Safe visual fixture: never imports a runner or performs Git/GitHub operations."""
    mock=True
    def __init__(self,config): self.config=deepcopy(config); self.names={'OPENAI_API_KEY','RESEND_API_KEY'}
    def inspect(self): return RepoTarget(Path('.'),'news/mock','mock','a'*40,'mock',False,frozenset(self.names),serialize_config(self.config))
    def save(self,target,config): self.config=deepcopy(config); return SaveResult(True,'MOCK ONLY: no file, commit, push or live secret was changed.',verified_head='a'*40)
    def refresh(self,target,config,result):
        current=self.inspect()
        if current.head!=result.verified_head or current.config_text!=serialize_config(config) or (current.repository,current.branch,current.remote)!=(target.repository,target.branch,target.remote): raise ValueError('Unexpected mock change')
        return current
    def secrets(self,target,values):
        self.names.update(k for k,v in values.items() if v)
        return [SecretUpdateResult(k,True,'MOCK ONLY: no secret uploaded.') for k,v in values.items() if v]


def create_server(adapter):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass  # Never log requests, bodies, tokens or CLI diagnostics.
        def reply(self,status,data,kind='application/json'):
            body=json.dumps(data).encode() if kind=='application/json' else data
            self.send_response(status)
            for key,value in {'Content-Type':kind+'; charset=utf-8','Content-Length':str(len(body)),'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer','Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"}.items(): self.send_header(key,value)
            self.end_headers(); self.wfile.write(body)
        def allowed(self,*,write=False):
            hosts=self.headers.get_all('Host',[])
            if hosts!=[self.server.host]: return False
            origin=self.headers.get_all('Origin',[])
            if origin and origin!=[self.server.origin]: return False
            if write or self.path=='/state':
                if write and origin!=[self.server.origin]: return False
                tokens=self.headers.get_all('X-Setup-Token',[])
                if len(tokens)!=1 or not secrets.compare_digest(tokens[0],self.server.token): return False
            if self.headers.get('Sec-Fetch-Site') in {'cross-site','same-site'}: return False
            return True
        def do_GET(self):
            if not self.allowed(): return self.reply(403,{'error':'Request rejected.'})
            if self.path=='/state':
                try:
                    t=self.server.adapter.inspect()
                    return self.reply(200,{'config':asdict(self.server.adapter.config),'repository':t.repository,'branch':t.branch,'default_branch':t.branch,'protected':t.protected,'secrets':sorted(t.secrets),'url':t.url,'mock':getattr(self.server.adapter,'mock',False)})
                except ValueError: return self.reply(400,{'error':'Repository unavailable or changed. Restart setup.'})
            assets={'/':('index.html','text/html'),'/app.js':('app.js','text/javascript'),'/style.css':('style.css','text/css')}
            if self.path not in assets: return self.reply(404,{'error':'Unknown path.'})
            name,kind=assets[self.path]; content=files('dochunt').joinpath('setup_ui',name).read_bytes()
            if name=='index.html': content=content.replace(b'__SESSION_TOKEN__',self.server.token.encode())
            self.reply(200,content,kind)
        def do_POST(self):
            if not self.allowed(write=True): return self.reply(403,{'error':'Request rejected.'})
            try:
                if self.headers.get('Content-Type')!='application/json' or self.headers.get('Transfer-Encoding'): raise ValueError()
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=200000: raise ValueError()
                body=json.loads(self.rfile.read(size))
                if not isinstance(body,dict): raise ValueError()
                adapter=self.server.adapter
                if self.path=='/plan':
                    if set(body)-{'config','enable','reviewed_preview','secret_updates'}: raise ValueError()
                    enable=body.get('enable',False)
                    if type(enable) is not bool or (enable and body.get('reviewed_preview') is not True): raise ValueError('Review a paid preview and approved inbox test before requesting activation.')
                    t=adapter.inspect(); c=form_config(body['config'],adapter.config,enable=enable)
                    if enable: require_secrets(c,t.secrets)
                    updates=body.get('secret_updates',[])
                    if not isinstance(updates,list) or any(not isinstance(v,str) for v in updates) or len(set(updates))!=len(updates) or set(updates)-set(c.secret_names.values()) or (enable and updates): raise ValueError('Review selected provider secret names separately.')
                    changes=[]
                    old=asdict(adapter.config); new=asdict(c)
                    for section,record in new.items():
                        if record!=old[section]: changes.append({'section':section,'before':old[section],'after':record})
                    if not changes: changes=[{'section':'schedule','before':old['schedule'],'after':new['schedule']}]
                    ident=secrets.token_urlsafe(24)
                    self.server.plan=(ident,t,c,enable,frozenset(updates))
                    return self.reply(200,{'plan_id':ident,'repository':t.repository,'branch':t.branch,'default_branch':t.branch,'changes':changes,'toml':serialize_config(c),'enable':enable,'protected':t.protected,'secret_updates':updates})
                if self.path in {'/save','/enable'}:
                    if set(body)-{'plan_id','confirm_repository','confirm_branch','secrets'}: raise ValueError()
                    plan=self.server.plan
                    if not plan or body.get('plan_id')!=plan[0] or body.get('confirm_repository')!=plan[1].repository or body.get('confirm_branch')!=plan[1].branch or (self.path=='/enable')!=plan[3]: raise ValueError('Review changes and explicitly confirm the displayed repository and branch.')
                    _,target,c,enable,approved_secrets=plan
                    try:
                        current=adapter.inspect()
                        if current!=target: raise ValueError()
                    except ValueError:
                        self.server.plan=None
                        return self.reply(200,{'config':asdict(SaveResult(False,'Repository changed after review. No writes attempted. Restart setup or propose this configuration through a manual pull request.',serialize_config(c))),'secrets':[],'complete':False,'missing_secrets':[],'schedule_enabled':None,'url':target.url})
                    values=body.get('secrets',{})
                    if not isinstance(values,dict) or set(values)-set(c.secret_names.values()) or any(not isinstance(v,str) or len(v)>64000 for v in values.values()): raise ValueError('Use only the selected providers secret fields.')
                    if {name for name,value in values.items() if value}!=approved_secrets: raise ValueError('Secret selection changed after review.')
                    if enable and values: raise ValueError('Save replacement secrets with scheduling disabled first.')
                    self.server.plan=None
                    result=adapter.save(target,c)
                    updates=[]; verified=None
                    if result.pushed:
                        try: verified=adapter.refresh(target,c,result)
                        except ValueError: pass
                    if verified is not None:
                        updates=adapter.secrets(verified,values)
                        schedule_enabled=c.schedule.enabled
                    else:
                        updates=[SecretUpdateResult(name,False,'Not attempted: configuration publication or verification failed. Existing secrets preserved.') for name in approved_secrets]
                        schedule_enabled=None
                        try:
                            observed=adapter.inspect()
                            schedule_enabled=config_from_data(tomllib.loads(observed.config_text)).schedule.enabled
                        except (ValueError,OSError): pass
                    values.clear(); body.clear()
                    configured=set(target.secrets)|{v.name for v in updates if v.success}
                    missing=sorted(set(c.secret_names.values())-configured)
                    return self.reply(200,{'config':asdict(result),'secrets':[asdict(v) for v in updates],'complete':verified is not None and all(v.success for v in updates) and not missing,'missing_secrets':missing,'schedule_enabled':schedule_enabled,'url':target.url})
                return self.reply(404,{'error':'Unknown path.'})
            except ModelConfigurationError as exc:
                self.reply(400,{'error':str(exc)})
            except (ValueError,KeyError,TypeError,OSError):
                self.reply(400,{'error':'Invalid or stale request. Check settings, repository confirmation, and preview requirements; no successful save is claimed.'})
        def reject_method(self): self.reply(405,{'error':'Method not allowed.'})
        do_PUT=do_DELETE=do_PATCH=do_OPTIONS=do_HEAD=reject_method
        def setup(self):
            super().setup(); self.connection.settimeout(10)
    server=HTTPServer(('127.0.0.1',0),Handler)
    server.adapter=adapter; server.token=secrets.token_urlsafe(32); server.plan=None
    server.host=f'127.0.0.1:{server.server_address[1]}'; server.origin='http://'+server.host
    return server


def launch(path,*,mock=False,open_browser=True):
    adapter=MockAdapter(load_config(Path(path)/'newsroom.toml')) if mock else GitHubAdapter(path)
    with create_server(adapter) as server:
        print(('SAFE MOCK — no GitHub operations. ' if mock else '')+f'Open {server.origin}/ ; stop with Ctrl-C. Closing the wizard has no effect on GitHub Actions.')
        if open_browser: webbrowser.open(server.origin+'/')
        try: server.serve_forever()
        except KeyboardInterrupt: pass

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(); parser.add_argument('--mock',action='store_true',required=True); parser.add_argument('--repo',type=Path,default=Path('.')); parser.add_argument('--no-browser',action='store_true'); args=parser.parse_args()
    launch(args.repo,mock=True,open_browser=not args.no_browser)
