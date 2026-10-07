import subprocess
import pytest
from dochunt.state import StateStore


def test_concurrent_git_state_change_blocks_send(tmp_path):
    def git(*a): subprocess.run(['git',*a],cwd=tmp_path,check=True,capture_output=True)
    git('init'); git('config','user.email','test@example.invalid'); git('config','user.name','Test')
    (tmp_path/'seed').write_text('seed'); git('add','seed'); git('commit','-m','seed')
    s=StateStore(tmp_path/'data',repo=tmp_path)
    (tmp_path/'seed').write_text('changed'); git('add','seed'); git('commit','-m','other')
    with pytest.raises(RuntimeError): s.save({'attempts':{}})


def test_state_commit_excludes_unrelated_staged_code(tmp_path):
    def git(*a): return subprocess.run(['git',*a],cwd=tmp_path,check=True,capture_output=True,text=True).stdout
    git('init'); git('config','user.email','test@example.invalid'); git('config','user.name','Test')
    (tmp_path/'data').mkdir(); (tmp_path/'data/state.json').write_text('{}\n'); (tmp_path/'data/history.jsonl').write_text('')
    git('add','data'); git('commit','-m','seed')
    (tmp_path/'unrelated.py').write_text('private code'); git('add','unrelated.py')
    s=StateStore(tmp_path/'data',repo=tmp_path); s.save({'attempts':{},'completed':{},'history':[]})
    assert git('diff','--cached','--name-only').strip()=='unrelated.py'
    assert 'unrelated.py' not in git('show','--format=','--name-only','HEAD')


def test_concurrent_commit_after_precheck_preserved_and_no_send(tmp_path,monkeypatch):
    from test_delivery import prepared, Fake
    from dochunt.delivery import send
    import json
    def git(*a): return subprocess.run(['git',*a],cwd=tmp_path,check=True,capture_output=True,text=True).stdout
    git('init'); git('config','user.email','test@example.invalid'); git('config','user.name','Test')
    (tmp_path/'data').mkdir(); (tmp_path/'data/state.json').write_text('{}\n'); (tmp_path/'data/history.jsonl').write_text('')
    git('add','data'); git('commit','-m','seed')
    store=StateStore(tmp_path/'data',repo=tmp_path); original=store._git; competing=[]
    def injected(*args,**kwargs):
        result=original(*args,**kwargs)
        if args[0]=='status' and not competing:
            (tmp_path/'data/state.json').write_text('{"competing":true}\n')
            git('add','data/state.json'); git('commit','-m','competing')
            competing.append(git('rev-parse','HEAD').strip())
        return result
    monkeypatch.setattr(store,'_git',injected); transport=Fake()
    with pytest.raises(RuntimeError): send(prepared(),store,transport)
    assert not transport.calls
    assert git('rev-parse','HEAD').strip()==competing[0]
    assert json.loads((tmp_path/'data/state.json').read_text())=={'competing':True}
