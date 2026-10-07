"""Private tracked state with atomic writes and an exclusive delivery lock."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import subprocess
import tempfile
import uuid


class StateStore:
    def __init__(self,path,*,repo=None,git=True):
        self.path=Path(path).resolve(); self.repo=Path(repo or self.path.parent).resolve(); self.git=git
        self.head=self._git('rev-parse','HEAD').strip() if git else None
    def _git(self,*args,input=None,env=None):
        return subprocess.run(['git',*args],cwd=self.repo,check=True,capture_output=True,text=True,input=input,env=env).stdout
    @contextmanager
    def locked(self):
        self.path.mkdir(parents=True,exist_ok=True,mode=0o700)
        with (self.path/'.lock').open('a') as stream:
            fcntl.flock(stream,fcntl.LOCK_EX)
            yield
    def read(self):
        file=self.path/'state.json'
        data=json.loads(file.read_text()) if file.exists() and file.stat().st_size else {}
        if not isinstance(data,dict): raise ValueError('Invalid private state')
        for key,default in [('attempts',{}),('completed',{}),('history',[])]: data.setdefault(key,default)
        return data
    def check_head(self):
        if self.git and self._git('rev-parse','HEAD').strip()!=self.head:
            raise RuntimeError('Git changed before delivery')
    def _commit_message(self):
        return 'Persist DocHunt delivery state\n'
    def save(self,data):
        if self.git:
            if self._git('rev-parse','HEAD').strip()!=self.head: raise RuntimeError('Git changed; reload state before delivery')
            paths=[str((self.path/name).relative_to(self.repo)) for name in ('state.json','history.jsonl')]
            if self._git('status','--porcelain','--',*paths).strip(): raise RuntimeError('State has uncommitted changes')
        values=[('state.json',json.dumps(data,sort_keys=True,indent=2)+'\n'),('history.jsonl',''.join(json.dumps(row,sort_keys=True)+'\n' for row in data.get('history',[])))]
        if self.git:
            # Build a tree from the expected parent without modifying the real index
            # or worktree; update-ref rejects a competing revision atomically.
            with tempfile.TemporaryDirectory() as temporary:
                env={**os.environ,'GIT_INDEX_FILE':str(Path(temporary)/'index')}
                self._git('read-tree',self.head,env=env)
                blobs=[]
                for name,value in values:
                    blob=self._git('hash-object','-w','--stdin',input=value).strip()
                    path=str((self.path/name).relative_to(self.repo)); blobs.append((path,blob))
                    self._git('update-index','--add','--cacheinfo',f'100644,{blob},{path}',env=env)
                tree=self._git('write-tree',env=env).strip()
                commit=self._git('commit-tree',tree,'-p',self.head,input=self._commit_message()).strip()
                try: self._git('update-ref','HEAD',commit,self.head)
                except subprocess.CalledProcessError as exc:
                    raise RuntimeError('Competing Git revision; delivery stopped') from exc
                self.head=commit
                for path,blob in blobs:
                    self._git('update-index','--add','--cacheinfo',f'100644,{blob},{path}')
        self.path.mkdir(parents=True,exist_ok=True,mode=0o700)
        for name,value in values:
            target=self.path/name; temporary=self.path/(name+'.tmp')
            with temporary.open('w') as stream:
                os.chmod(temporary,0o600); stream.write(value); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary,target)
        fd=os.open(self.path,os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
        self.check_head()


class PublishedStateStore(StateStore):
    """Every successful save is durable on a private installation's branch.

    Normal fast-forward push arbitrates competing checkouts; never force/rebase.
    A rejected publication leaves local evidence and raises before transport.
    """
    def __init__(self,path,*,repo=None,remote='origin',branch='main'):
        super().__init__(path,repo=repo)
        self.remote=remote; self.branch=branch
        if not remote or remote.startswith('-'): raise ValueError('Invalid remote')
        self._git('check-ref-format',f'refs/heads/{branch}')
        self.check_head()
    def check_head(self):
        super().check_head()
        if hasattr(self,'remote') and not getattr(self,'_publishing',False):
            tip=self._git('ls-remote','--exit-code',self.remote,f'refs/heads/{self.branch}').split()[0]
            if tip!=self.head: raise RuntimeError('Remote changed or state was not published; reload checkout')
    def _commit_message(self):
        # Equal trees/parents/authors/timestamps must never let two checkouts
        # publish one identical sending commit and both claim the provider call.
        return f'Persist DocHunt delivery state\n\nPublication owner: {uuid.uuid4()}\n'
    def save(self,data):
        self.check_head()
        # Base save calls check_head after local commit. Defer remote comparison
        # until that exact commit is pushed, without suppressing local CAS checks.
        self._publishing=True
        try:
            super().save(data)
            self._git('push',self.remote,f'{self.head}:refs/heads/{self.branch}')
        except subprocess.CalledProcessError as exc:
            raise RuntimeError('State publication rejected; no automatic recovery or force push') from exc
        finally: self._publishing=False
        self.check_head()
