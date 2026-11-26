from dataclasses import asdict
import http.client
import json
from pathlib import Path
import threading
import pytest
from dochunt.config import load_config
from dochunt.setup_server import create_server, form_config, MockAdapter
ROOT=Path(__file__).parents[1]

def test_invalid_form_fields_shared_validation():
    c=load_config(ROOT/'examples'/'military.toml'); payload=asdict(c); payload['schedule']['timezone']='wrong'
    with pytest.raises(ValueError): form_config(payload,c)

def test_initial_save_keeps_schedule_disabled():
    c=load_config(ROOT/'examples'/'military.toml'); payload=asdict(c); payload['schedule']['enabled']=True
    assert form_config(payload,c).schedule.enabled is False

def test_preserve_unedited_agent_beat_and_email_fields():
    c=load_config(ROOT/'examples'/'military.toml'); c.agent.max_budget_usd=7.5; c.beat.priorities=['One','Two']; c.email.smtp_host='mail.example.org'
    assert asdict(form_config(asdict(c),c))==asdict(c)

@pytest.fixture
def server():
    s=create_server(MockAdapter(load_config(ROOT/'examples'/'military.toml'))); thread=threading.Thread(target=s.serve_forever,daemon=True); thread.start()
    yield s
    s.shutdown(); s.server_close(); thread.join()

def request(s,method,path,body=None,headers=None):
    conn=http.client.HTTPConnection(*s.server_address); conn.request(method,path,json.dumps(body) if body is not None else None,headers or {}); resp=conn.getresponse(); data=resp.read(); conn.close(); return resp.status,data

def auth(s): return {'Origin':s.origin,'Host':s.host,'X-Setup-Token':s.token,'Content-Type':'application/json'}

def test_loopback_binding(server): assert server.server_address[0]=='127.0.0.1'

def test_session_token_origin_host_and_methods(server):
    for change in [{'Origin':'https://evil.test'},{'Host':'evil.test'},{'X-Setup-Token':'wrong'},{'Origin':''}]:
        headers=auth(server); headers.update(change); assert request(server,'POST','/plan',{},headers)[0]==403
    for method in ['PUT','DELETE','PATCH','OPTIONS','HEAD']:
        assert request(server,method,'/plan',{},auth(server))[0] in {403,405}

def test_html_and_storage_credential_free(server):
    status,html=request(server,'GET','/'); assert status==200 and b'Repository secrets' in html
    status,js=request(server,'GET','/app.js'); assert status==200 and b"input.type = 'password'" in js
    assert all(x not in js for x in [b'localStorage',b'sessionStorage',b'indexedDB'])
    assert b'src="https://' not in html and b'href="https://' not in html

def test_explicit_confirmation_and_enable_gate(server):
    c=asdict(server.adapter.config)
    assert request(server,'POST','/save',{'config':c},auth(server))[0]==400
    status,data=request(server,'POST','/plan',{'config':c},auth(server)); plan=json.loads(data); assert status==200 and plan['changes']
    status,data=request(server,'POST','/save',{'plan_id':plan['plan_id'],'confirm_repository':'news/mock','confirm_branch':'mock','secrets':{}},auth(server)); assert status==200 and json.loads(data)['config']['pushed']
    assert not server.adapter.config.schedule.enabled
    assert request(server,'POST','/enable',{'reviewed_preview':False},auth(server))[0]==400

def test_no_secret_echo_in_response(server):
    status,data=request(server,'POST','/plan',{'config':asdict(server.adapter.config),'secret_updates':['OPENAI_API_KEY']},auth(server)); p=json.loads(data)
    status,data=request(server,'POST','/save',{'plan_id':p['plan_id'],'confirm_repository':'news/mock','confirm_branch':'mock','secrets':{'OPENAI_API_KEY':'SENSITIVE-fixture'}},auth(server)); assert status==200 and b'SENSITIVE' not in data

def test_enable_only_after_preview_confirmation_and_required_secrets(server):
    c=asdict(server.adapter.config); c['agent']['runtime']='claude'; c['agent']['model']='claude-sonnet-5-5'
    assert request(server,'POST','/plan',{'config':c,'enable':True,'reviewed_preview':True},auth(server))[0]==400
    c=asdict(server.adapter.config)
    status,data=request(server,'POST','/plan',{'config':c,'enable':True,'reviewed_preview':True},auth(server)); p=json.loads(data); assert status==200
    status,data=request(server,'POST','/enable',{'plan_id':p['plan_id'],'confirm_repository':'news/mock','confirm_branch':'mock'},auth(server)); assert status==200 and json.loads(data)['schedule_enabled']

def test_missing_secret_cannot_claim_completed(server):
    c=asdict(server.adapter.config); c['agent']['runtime']='claude'; c['agent']['model']='claude-sonnet-5-5'
    status,data=request(server,'POST','/plan',{'config':c},auth(server)); p=json.loads(data)
    status,data=request(server,'POST','/save',{'plan_id':p['plan_id'],'confirm_repository':'news/mock','confirm_branch':'mock'},auth(server)); result=json.loads(data)
    assert not result['complete'] and result['missing_secrets']==['ANTHROPIC_API_KEY'] and not result['schedule_enabled']

def test_wrong_confirmation_and_consumed_plan_rejected(server):
    status,data=request(server,'POST','/plan',{'config':asdict(server.adapter.config)},auth(server)); p=json.loads(data)
    body={'plan_id':p['plan_id'],'confirm_repository':'other/repo','confirm_branch':'mock'}
    assert request(server,'POST','/save',body,auth(server))[0]==400
    body['confirm_repository']='news/mock'
    assert request(server,'POST','/save',body,auth(server))[0]==200
    assert request(server,'POST','/save',body,auth(server))[0]==400

def test_secrets_not_in_toml_html_or_server_logs(server,capsys):
    status,data=request(server,'POST','/plan',{'config':asdict(server.adapter.config),'secret_updates':['OPENAI_API_KEY']},auth(server)); p=json.loads(data)
    body={'plan_id':p['plan_id'],'confirm_repository':'news/mock','confirm_branch':'mock','secrets':{'OPENAI_API_KEY':'SENSITIVE-fixture'}}
    assert request(server,'POST','/save',body,auth(server))[0]==200
    assert 'SENSITIVE' not in repr(server.adapter.__dict__)
    assert b'SENSITIVE' not in request(server,'GET','/')[1]
    from dochunt.github_setup import serialize_config
    assert 'SENSITIVE' not in serialize_config(server.adapter.config)
    assert 'SENSITIVE' not in repr(capsys.readouterr())

def test_request_bounds_and_unknown_routes(server):
    assert request(server,'POST','/anything',{},auth(server))[0]==404
    headers=auth(server); headers['Content-Length']='200001'
    assert request(server,'POST','/plan',{},headers)[0]==400
    assert request(server,'GET','/state')[0]==403

def test_unreviewed_secret_selection_cannot_write(server):
    status,data=request(server,'POST','/plan',{'config':asdict(server.adapter.config)},auth(server)); p=json.loads(data)
    body={'plan_id':p['plan_id'],'confirm_repository':'news/mock','confirm_branch':'mock','secrets':{'OPENAI_API_KEY':'SENSITIVE-fixture'}}
    assert request(server,'POST','/save',body,auth(server))[0]==400

def test_stale_confirmation_offers_manual_toml_without_write(server):
    status,data=request(server,'POST','/plan',{'config':asdict(server.adapter.config)},auth(server)); p=json.loads(data)
    server.adapter.config.identity.newsletter_name='Concurrent change'
    status,data=request(server,'POST','/save',{'plan_id':p['plan_id'],'confirm_repository':'news/mock','confirm_branch':'mock'},auth(server)); result=json.loads(data)
    assert status==200 and not result['config']['pushed'] and result['config']['manual_toml']
    assert server.adapter.config.identity.newsletter_name=='Concurrent change'

def save_with_replacements(server):
    status,data=request(server,'POST','/plan',{'config':asdict(server.adapter.config),'secret_updates':['OPENAI_API_KEY','RESEND_API_KEY']},auth(server)); p=json.loads(data)
    return request(server,'POST','/save',{'plan_id':p['plan_id'],'confirm_repository':'news/mock','confirm_branch':'mock','secrets':{'OPENAI_API_KEY':'fixture','RESEND_API_KEY':'fixture'}},auth(server))

def test_enabled_blocked_disable_never_updates_secrets(server):
    from dochunt.github_setup import SaveResult
    server.adapter.config.schedule.enabled=True; calls=[]
    server.adapter.save=lambda target,config: SaveResult(False,'blocked')
    server.adapter.secrets=lambda *args: calls.append('secret') or []
    status,data=save_with_replacements(server); result=json.loads(data)
    assert not calls and result['schedule_enabled'] is True and not result['complete']

def test_disable_verified_before_partial_secret_updates(server):
    from dochunt.github_setup import SecretUpdateResult
    server.adapter.config.schedule.enabled=True; calls=[]
    original=server.adapter.save
    def save(target,config): calls.append('save'); return original(target,config)
    def updates(target,values):
        assert server.adapter.config.schedule.enabled is False
        assert target.config_text==__import__('dochunt.github_setup',fromlist=['serialize_config']).serialize_config(server.adapter.config)
        calls.append('secrets'); return [SecretUpdateResult('OPENAI_API_KEY',True,'updated'),SecretUpdateResult('RESEND_API_KEY',False,'failed')]
    server.adapter.save=save; server.adapter.secrets=updates
    status,data=save_with_replacements(server); result=json.loads(data)
    assert calls==['save','secrets'] and result['schedule_enabled'] is False and not result['complete']

def test_list_fields_and_human_review():
    html=(ROOT/'src/dochunt/setup_ui/index.html').read_text(); js=(ROOT/'src/dochunt/setup_ui/app.js').read_text()
    assert 'id="priorities"' in html and 'id="source-families"' in html and 'name="agent.runtime"' in html
    assert "JSON.parse(v)" not in js and "JSON.stringify(v.before" not in js
    assert 'Coverage brief' in js and 'Previous' in js and 'New' in js
    assert 'firecrawl' not in (html+js).lower() and 'resource' not in html.lower()

def test_unverified_disable_and_unreadable_schedule_skip_secrets(server):
    from dochunt.github_setup import SaveResult
    calls=[]; original_inspect=server.adapter.inspect
    def save(target,config):
        def unavailable(): raise ValueError('unavailable')
        server.adapter.inspect=unavailable
        return SaveResult(False,'uncertain')
    server.adapter.save=save; server.adapter.secrets=lambda *args: calls.append('secret') or []
    status,data=save_with_replacements(server); result=json.loads(data)
    assert not calls and result['schedule_enabled'] is None and not result['complete']

def test_refresh_cannot_accept_unrelated_changes_before_secrets(server):
    calls=[]; original=server.adapter.save
    def save(target,config):
        result=original(target,config); server.adapter.config.identity.newsletter_name='Concurrent unrelated change'; return result
    server.adapter.save=save; server.adapter.secrets=lambda *args: calls.append('secret') or []
    status,data=save_with_replacements(server); assert not calls and not json.loads(data)['complete']

def test_list_ui_and_readable_review_with_node():
    import shutil, subprocess
    node=shutil.which('node')
    if not node: pytest.skip('Node available for local UI logic verification; browser verification remains required')
    script=r'''
const fs=require('fs'), vm=require('vm'), assert=require('assert');
const elements=new Map();
function element(tag='div') { return {tag,dataset:{},children:[],value:'',append(...values){this.children.push(...values)},replaceChildren(){this.children=[]},addEventListener(){}}; }
const document={querySelector:()=>({content:'fixture'}),getElementById:id=>{if(!elements.has(id))elements.set(id,element());return elements.get(id)},createElement:element,querySelectorAll:()=>[]};
const context={document,fetch:()=>new Promise(()=>{}),structuredClone,console}; vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),context);
document.getElementById('priorities').value=' Safety first \n\n Money ';
vm.runInContext('result=lines("priorities"); summary=readableChanges([{section:"beat",before:{brief:"old",priorities:["a"]},after:{brief:"new\\nline",priorities:["a","b"]}}]);',context);
assert.deepStrictEqual(JSON.parse(JSON.stringify(context.result)),['Safety first','Money']);
assert(context.summary.includes('Coverage brief\nPrevious: old\nNew: new\nline'));
assert(context.summary.includes('Priorities\nPrevious: a\nNew: a\nb'));
vm.runInContext('subText=audienceText({subscription_url:"https://lists.example.org",subscription_domains:["newsroom.org"],recipients:["ed@example.org"]}); fixedText=audienceText({subscription_url:"",subscription_domains:[],recipients:["a@x.example","b@x.example"]});',context);
assert(context.subText.includes('subscription list at https://lists.example.org') && context.subText.includes('newsroom.org') && context.subText.includes('only if they are on the list'));
assert.equal(context.fixedText,'a@x.example, b@x.example');
'''
    result=subprocess.run([node,'-e',script,str(ROOT/'src/dochunt/setup_ui/app.js')],capture_output=True,text=True)
    assert result.returncode==0,result.stderr


def test_agent_switch_requires_explicit_save_and_its_secret(server,capsys):
    data=asdict(server.adapter.config); data['agent']['runtime']='claude'; data['agent']['model']=''
    status,body=request(server,'POST','/plan',{'config':data,'secret_updates':['ANTHROPIC_API_KEY']},auth(server))
    assert status==200; plan=json.loads(body)
    assert server.adapter.config.agent.runtime=='codex'
    assert request(server,'POST','/save',{'plan_id':plan['plan_id'],'confirm_repository':'wrong','confirm_branch':'mock','secrets':{'ANTHROPIC_API_KEY':'SENSITIVE-anthropic-fixture'}},auth(server))[0]==400
    status,body=request(server,'POST','/save',{'plan_id':plan['plan_id'],'confirm_repository':'news/mock','confirm_branch':'mock','secrets':{'ANTHROPIC_API_KEY':'SENSITIVE-anthropic-fixture'}},auth(server))
    assert status==200 and b'SENSITIVE' not in body
    assert server.adapter.config.agent.runtime=='claude' and not server.adapter.config.schedule.enabled
    assert 'ANTHROPIC_API_KEY' in server.adapter.names
    assert 'SENSITIVE' not in repr(capsys.readouterr())
    assert b'agent.runtime' in request(server,'GET','/')[1]


@pytest.mark.parametrize('field,value',[('model','SENSITIVE invalid!'),('timeout_minutes',90)])
def test_invalid_agent_plan_has_safe_field_error_and_no_writes(server,monkeypatch,field,value):
    config=asdict(server.adapter.config); config['agent'][field]=value
    monkeypatch.setattr(server.adapter,'save',lambda *a: pytest.fail('configuration write'))
    monkeypatch.setattr(server.adapter,'secrets',lambda *a: pytest.fail('secret write'))
    for enable in (False,True):
        status,data=request(server,'POST','/plan',{'config':config,'enable':enable,'reviewed_preview':enable},auth(server))
        assert status==400
        error=json.loads(data)['error']
        assert 'agent.'+field in error
        assert str(value) not in error
    assert not server.plan
    assert request(server,'POST','/save',{'plan_id':'invalid','confirm_repository':'news/mock','confirm_branch':'mock'},auth(server))[0]==400
