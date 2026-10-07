from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import pytest
from dochunt.config import load_config
from dochunt.github_setup import inspect_target, save_config, set_secrets, serialize_config, require_secrets

ROOT=Path(__file__).parents[1]
class Runner:
    def __init__(self): self.calls=[]; self.dirty=False; self.head='a'*40; self.remote=self.head; self.private=True; self.owner='news'; self.protected=False; self.auth=True; self.fail_secret=None; self.push_fail=False
    def __call__(self,args,*,cwd,input=None):
        self.calls.append((args,input))
        if args[:3]==['gh','auth','status'] and not self.auth: raise RuntimeError('SECRET-sensitive diagnostic')
        if args[:3]==['git','status','--porcelain']: return ' M other.txt' if self.dirty else ''
        if args[:3]==['git','rev-parse','--show-toplevel']: return str(cwd)
        if args[:3]==['git','rev-parse','HEAD']: return self.head
        if args[:3]==['git','rev-parse','HEAD^']: return 'a'*40
        if args[:3]==['git','diff','--name-only']: return 'newsroom.toml'
        if args[:3]==['git','symbolic-ref','--short']: return 'work'
        if args[:4]==['git','remote','get-url','--push']: return 'git@github.com:news/own.git'
        if args[:3]==['git','remote','get-url']: return 'git@github.com:news/own.git'
        if args[:2]==['git','ls-remote']: return self.remote+'\trefs/heads/work\n'
        if args[:3]==['gh','api','user']: return json.dumps({'login':self.owner})
        if args[:3]==['gh','api','repos/news/own']: return json.dumps({'full_name':'news/own','private':self.private,'default_branch':'work','permissions':{'push':True}})
        if args[:3]==['gh','api','repos/news/own/branches/work']: return json.dumps({'protected':self.protected})
        if args[:3]==['gh','api','repos/news/own/rules/branches/work']: return '[]'
        if args[:3]==['gh','secret','list']: return json.dumps([{'name':'OPENAI_API_KEY'},{'name':'RESEND_API_KEY'}])
        if args[:3]==['gh','secret','set'] and args[3]==self.fail_secret: raise RuntimeError('SECRET-sensitive diagnostic')
        if 'commit' in args: self.head='b'*40
        if 'push' in args and self.push_fail: raise RuntimeError('rejected')
        if 'push' in args: self.remote=self.head
        return ''
@pytest.fixture
def repo(tmp_path):
    (tmp_path/'newsroom.toml').write_text((ROOT/'newsroom.toml').read_text()); return tmp_path

def test_serialization_roundtrip_preserves_all_fields(repo):
    c=load_config(repo/'newsroom.toml'); c.editorial.coverage_brief='line one\n"quoted" \\ line'; c.email.smtp_port=465
    (repo/'round.toml').write_text(serialize_config(c)); assert asdict(load_config(repo/'round.toml'))==asdict(c)

def test_inspect_private_identity_and_branch(repo):
    t=inspect_target(repo,runner=Runner()); assert t.repository=='news/own' and t.branch=='work' and t.secrets==frozenset({'OPENAI_API_KEY','RESEND_API_KEY'})

@pytest.mark.parametrize('field,value',[('dirty',True),('remote','c'*40),('private',False),('owner','someone'),('auth',False)])
def test_unsafe_target_fails_closed(repo,field,value):
    r=Runner(); setattr(r,field,value)
    with pytest.raises(ValueError): inspect_target(repo,runner=r)
    assert not any(a[:2] in (['git','push'],['git','commit']) for a,_ in r.calls)

def test_protected_branch_manual_fallback(repo):
    r=Runner(); r.protected=True; t=inspect_target(repo,runner=r); before=(repo/'newsroom.toml').read_text()
    c=load_config(repo/'newsroom.toml'); c.identity.newsletter_name='Changed'
    result=save_config(t,c,runner=r); assert not result.pushed and result.manual_toml and (repo/'newsroom.toml').read_text()==before

def test_config_only_commit_and_push(repo):
    r=Runner(); t=inspect_target(repo,runner=r); c=load_config(repo/'newsroom.toml'); c.identity.newsletter_name='Changed'
    result=save_config(t,c,runner=r); assert result.pushed
    assert ['git','add','--','newsroom.toml'] in [a for a,_ in r.calls]
    assert ['git','-c','core.hooksPath=/dev/null','push','origin','HEAD:refs/heads/work'] in [a for a,_ in r.calls]

def test_stale_target_after_confirmation_preserves_manual_config(repo):
    r=Runner(); t=inspect_target(repo,runner=r); r.remote='c'*40
    result=save_config(t,load_config(repo/'newsroom.toml'),runner=r); assert not result.pushed and result.manual_toml

def test_partial_secret_failure_and_stdin_only(repo):
    r=Runner(); t=inspect_target(repo,runner=r); r.fail_secret='RESEND_API_KEY'
    results=set_secrets(t,{'OPENAI_API_KEY':'SENSITIVE-fixture','RESEND_API_KEY':'SENSITIVE-fixture','SMTP_PASSWORD':''},runner=r)
    assert [v.success for v in results]==[True,False]
    assert 'SENSITIVE' not in repr(results)
    for args,value in r.calls:
        assert 'SENSITIVE' not in repr(args)
        if value: assert args[:3]==['gh','secret','set']

def test_provider_change_requires_new_secret(repo):
    c=load_config(repo/'newsroom.toml'); c.ai.provider='anthropic'
    with pytest.raises(ValueError): require_secrets(c,{'OPENAI_API_KEY','RESEND_API_KEY'})

def test_push_failure_never_claims_success(repo):
    r=Runner(); t=inspect_target(repo,runner=r); r.push_fail=True; c=load_config(repo/'newsroom.toml'); c.identity.newsletter_name='Changed'
    result=save_config(t,c,runner=r); assert not result.pushed and result.manual_toml

def test_secret_blank_preserves_and_unknown_rejected(repo):
    r=Runner(); t=inspect_target(repo,runner=r); count=len(r.calls)
    assert set_secrets(t,{'OPENAI_API_KEY':''},runner=r)==[] and len(r.calls)==count
    with pytest.raises(ValueError): set_secrets(t,{'GH_TOKEN':'not-allowed'},runner=r)

def test_wrong_push_remote_and_unreadable_rules_rejected(repo):
    r=Runner()
    def altered(args,**kwargs):
        if args[:4]==['git','remote','get-url','--push']: return 'git@github.com:other/repo.git'
        return r(args,**kwargs)
    with pytest.raises(ValueError): inspect_target(repo,runner=altered)
    def unreadable(args,**kwargs):
        if 'repos/news/own/rules/branches/work' in args: raise ValueError('permission denied')
        return r(args,**kwargs)
    with pytest.raises(ValueError): inspect_target(repo,runner=unreadable)

def test_symlink_config_is_rejected(repo):
    r=Runner(); (repo/'outside').write_text((repo/'newsroom.toml').read_text()); (repo/'newsroom.toml').unlink(); (repo/'newsroom.toml').symlink_to(repo/'outside')
    with pytest.raises(ValueError): inspect_target(repo,runner=r)

def test_temporary_git_config_only_commit(tmp_path):
    """Real isolated Git repos; only GitHub metadata is mocked. No authenticated operations."""
    from dochunt.github_setup import run_command
    remote=tmp_path/'remote.git'; repo=tmp_path/'clone'
    def git(args,path): return run_command(['git',*args],cwd=path)
    git(['init','--bare',str(remote)],tmp_path); git(['clone',str(remote),str(repo)],tmp_path)
    git(['config','user.email','fixture@example.invalid'],repo); git(['config','user.name','Fixture'],repo)
    (repo/'newsroom.toml').write_text((ROOT/'newsroom.toml').read_text()); (repo/'untouched').write_text('keep')
    git(['add','.'],repo); git(['commit','-m','fixture'],repo); git(['push','origin','HEAD'],repo)
    def offline(args,*,cwd,input=None):
        if args[:3]==['git','remote','get-url']: return 'git@github.com:news/own.git'
        if args[:2]==['gh','api'] and '/branches/' in args[2]: return '[]' if '/rules/' in args[2] else '{"protected":false}'
        if args[:3]==['gh','api','repos/news/own']:
            return json.dumps({'full_name':'news/own','private':True,'default_branch':git(['symbolic-ref','--short','HEAD'],repo),'permissions':{'push':True}})
        if args[0]=='gh': return Runner()(args,cwd=cwd,input=input)
        return run_command(args,cwd=cwd,input=input)
    t=inspect_target(repo,runner=offline); c=load_config(repo/'newsroom.toml'); c.identity.newsletter_name='New title'
    result=save_config(t,c,runner=offline); assert result.pushed
    assert git(['show','--pretty=','--name-only','HEAD'],repo)=='newsroom.toml'
    assert git(['status','--porcelain'],repo)==''
    assert git(['rev-parse','HEAD'],repo)==git(['rev-parse',f'refs/heads/{t.branch}'],remote)

def test_nondefault_branch_rejected_before_all_writes(repo):
    r=Runner()
    def runner(args,**kwargs):
        if args[:3]==['gh','api','repos/news/own']:
            return json.dumps({'full_name':'news/own','private':True,'default_branch':'main','permissions':{'push':True}})
        return r(args,**kwargs)
    with pytest.raises(ValueError): inspect_target(repo,runner=runner)
    assert not any('push' in args or 'commit' in args or args[:3]==['gh','secret','set'] for args,_ in r.calls)

def test_post_push_unrelated_change_is_not_accepted(repo):
    r=Runner(); t=inspect_target(repo,runner=r); c=load_config(repo/'newsroom.toml'); c.identity.newsletter_name='Changed'
    def runner(args,**kwargs):
        result=r(args,**kwargs)
        if 'push' in args: r.head='c'*40; r.remote=r.head
        return result
    result=save_config(t,c,runner=runner)
    assert not result.pushed and result.manual_toml

def test_default_branch_change_blocks_secret_recheck(repo):
    r=Runner(); t=inspect_target(repo,runner=r)
    def runner(args,**kwargs):
        if args[:3]==['gh','api','repos/news/own']: return json.dumps({'full_name':'news/own','private':True,'default_branch':'other','permissions':{'push':True}})
        return r(args,**kwargs)
    results=set_secrets(t,{'OPENAI_API_KEY':'fixture'},runner=runner)
    assert not results[0].success and not any(args[:3]==['gh','secret','set'] for args,_ in r.calls)
