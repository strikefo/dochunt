"""Private tracked state with atomic writes and an exclusive delivery lock."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
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
        # The tracker lives in leads.jsonl so it stays readable and diff-friendly.
        tracker=self.path/'leads.jsonl'
        if tracker.exists(): data['leads']=[json.loads(line) for line in tracker.read_text().splitlines() if line.strip()]
        for key,default in [('attempts',{}),('completed',{}),('leads',[])]: data.setdefault(key,default)
        return data
    def check_head(self):
        if self.git and self._git('rev-parse','HEAD').strip()!=self.head:
            raise RuntimeError('Git changed before delivery')
    def _commit_message(self):
        return 'Persist DocHunt delivery state\n'
    def save(self,data):
        if self.git:
            if self._git('rev-parse','HEAD').strip()!=self.head: raise RuntimeError('Git changed; reload state before delivery')
            paths=[str((self.path/name).relative_to(self.repo)) for name in ('state.json','leads.jsonl')]
            if self._git('status','--porcelain','--',*paths).strip(): raise RuntimeError('State has uncommitted changes')
        values=[('state.json',json.dumps({k:v for k,v in data.items() if k!='leads'},sort_keys=True,indent=2)+'\n'),('leads.jsonl',''.join(json.dumps(row,sort_keys=True,ensure_ascii=False)+'\n' for row in data.get('leads',[])))]
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


GIT_REASONS = (
    (r'non-fast-forward|fetch first|\[rejected\]', 'the branch moved on GitHub (non-fast-forward)'),
    (r'GH006|protected branch', 'branch protection refused the push'),
    (r'GH013|rule violations?|repository rule', 'a repository ruleset refused the push'),
    (r'permission to .* denied|returned error: 403|\b403\b', 'the push token lacks permission (HTTP 403)'),
    (r'authentication failed|returned error: 401|\b401\b|could not read username', 'the push token was not accepted (HTTP 401)'),
    (r'pre-receive hook declined|hook declined', 'a server-side hook declined the push'),
    (r'could not resolve host|connection timed out|connection refused|failed to connect|network is unreachable', 'GitHub could not be reached'),
    (r'please tell me who you are|empty ident', 'git has no author identity configured'),
)


def _scrub(line):
    line = re.sub(r'[a-z][a-z0-9+.-]*://\S+', '<url>', line, flags=re.I)   # every URL, with any query or userinfo
    line = re.sub(r'\S*@\S*github\.com\S*', '<url>', line, flags=re.I)
    line = re.sub(r'[A-Za-z0-9_\-+/=]{24,}', '***', line)                  # anything token-shaped
    return line


GITHUB_HOSTS={'github.com','www.github.com','ssh.github.com'}
UNKNOWN_GITHUB='<unrecognized GitHub path>'


def github_repository(url):
    """owner/name when git would push this URL to github.com, UNKNOWN_GITHUB for an unparseable github.com
    path, else None. Only plain https/ssh URLs and user@host:path count; anything git could route through a
    remote helper ("scheme::address", other schemes) is not GitHub."""
    from urllib.parse import urlsplit
    url=url.strip()
    if '::' in url: return None                       # <transport>::<address> runs a remote helper
    if '://' in url:
        # git picks the transport from the scheme exactly as written, so only these spellings count.
        if not url.startswith(('https://','ssh://')): return None
        try: parts=urlsplit(url); host=(parts.hostname or '').lower(); path=parts.path
        except ValueError: return None
        if url.startswith('https://') and host=='ssh.github.com': return None
    else:
        # scp-like: git splits host from path at the FIRST colon, then drops an optional user@.
        if ':' not in url or '/' in url.split(':',1)[0]: return None      # a local path
        target,path=url.split(':',1)
        user,_,host=target.rpartition('@')
        if user and not re.fullmatch(r'[A-Za-z0-9._-]+',user): return None
        host=host.lower()
    if host not in GITHUB_HOSTS: return None
    m=re.fullmatch(r'/?([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?',path)
    if not m or {m.group(1),m.group(2)}&{'.','..'}: return UNKNOWN_GITHUB
    # owner/project.wiki is the project's wiki: public whenever the project is, yet the API lookup of
    # "project.wiki" as a repository returns 404, which would look private.
    if m.group(2).lower().endswith('.wiki'): return UNKNOWN_GITHUB
    return f'{m.group(1)}/{m.group(2)}'


def actions_private_repository():
    """owner/name (lowercase) of this GitHub Actions run's repository when the run's own event payload says
    it is private, else ''. The repository name alone is not evidence: any workflow can run in a public repo."""
    if os.environ.get('GITHUB_ACTIONS')!='true': return ''
    try:
        with open(os.environ['GITHUB_EVENT_PATH'],encoding='utf-8') as f: repo=json.load(f).get('repository')
    except (KeyError,OSError,ValueError,AttributeError): return ''
    if not isinstance(repo,dict) or repo.get('private') is not True: return ''
    name=str(repo.get('full_name') or '').lower()
    return name if name and name==os.environ.get('GITHUB_REPOSITORY','').lower() else ''


def push_config_env():
    """The environment for reading settings `git push` will use: GIT_CONFIG redirects only `git config`."""
    env=dict(os.environ); env.pop('GIT_CONFIG',None)
    return env


def is_local_git_path(url):
    """True when git treats this push URL as a local path (git's rule: no scheme, and no colon before the first slash)."""
    url=url.strip()
    if url.startswith('file://'): return True
    if '://' in url: return False
    head=url.split('/',1)[0]
    return ':' not in head


def refuse_public_github_remote(url,*,fail_closed=True,opener=None):
    """Delivery state holds recipients and unpublished email; never publish it to a public repository.

    A public GitHub repository answers an anonymous API request; a private one returns 404. If the
    answer is unclear (rate limit, outage, odd response) delivery stops when fail_closed. The hosted
    workflow passes fail_closed=False because it already runs only in private repositories.
    """
    import json, urllib.request
    from urllib.error import HTTPError
    repo=github_repository(url)
    if not repo: return
    if repo==UNKNOWN_GITHUB:
        raise RuntimeError("A push destination on GitHub has an unrecognized path, so its visibility can't be checked; delivery stopped. Use a plain https://github.com/owner/name remote.")
    request=urllib.request.Request(f'https://api.github.com/repos/{repo}',headers={'Accept':'application/vnd.github+json','User-Agent':'dochunt-state-guard'})
    try:
        with (opener or urllib.request.urlopen)(request,timeout=15) as response:
            data=json.loads(response.read(200_000))
    except HTTPError as exc:
        if exc.code==404: return   # private (or missing): invisible to anonymous requests
        data=None
    except (OSError,ValueError):
        data=None
    if isinstance(data,dict) and data.get('private') is True: return
    if isinstance(data,dict) and data.get('private') is False:
        raise RuntimeError(f'Refusing to publish delivery state to the public repository {repo}: it contains recipients and unpublished email. Use a private copy.')
    if fail_closed:
        raise RuntimeError(f'Could not confirm that {repo} is private (GitHub did not answer clearly); delivery stopped before saving anything. Try again shortly.')


def git_failure(exc):
    """A readable, credential-free description of a failed git command.

    Known failures become a plain reason. Otherwise only git's own message lines are
    kept, with URLs and token-shaped strings removed, so an unknown cause is still visible.
    """
    cmd = exc.cmd[1] if isinstance(exc.cmd, (list, tuple)) and len(exc.cmd) > 1 else 'git'
    raw = exc.stderr if isinstance(exc.stderr, str) else (exc.output if isinstance(exc.output, str) else '')
    reasons = [reason for pattern, reason in GIT_REASONS if re.search(pattern, raw, re.I)]
    head = f'git {cmd} exited with status {exc.returncode}'
    if reasons:
        return f'{head}: {"; ".join(dict.fromkeys(reasons))}'
    parts, size = [], 0
    for line in raw.splitlines():
        if size >= 300:
            break
        if not re.match(r'\s*(remote:|error:|fatal:|!|hint:)', line):
            continue
        line = '<long line omitted>' if len(line) > 400 else _scrub(line.strip())  # bounded work, no partial tokens
        parts.append(line)
        size += len(line) + 3
    detail = ' | '.join(parts)[:300]
    return f'{head}: {detail}' if detail else f'{head} (no details reported)'


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
        self._named=remote in self._git('remote').split()
        if not self._named:
            if not (Path(self.repo)/remote).is_dir():
                raise ValueError('Publish delivery state to a named git remote (such as origin) or a local folder')
            # Use one absolute path for the check and for every git command, so git can't read the
            # name as an ssh host ("host:path") or resolve it from a different directory.
            self.remote=str((Path(self.repo)/remote).resolve())
        destinations=self._destinations()
        # When GitHub itself says this Actions run belongs to a private repository (the same event field the
        # workflow gates on), an API hiccup must not block delivery to *that* repository. Every other
        # destination, and every run without that evidence, is checked strictly.
        own=actions_private_repository()
        for url in destinations:
            if is_local_git_path(url): continue
            target=github_repository(url)
            if target is None:
                # Another host, or an ssh alias for GitHub: its visibility can't be confirmed.
                raise ValueError('DocHunt publishes delivery state only to a private GitHub repository (https://github.com/owner/name or git@github.com:owner/name) or a local folder; one push destination is neither, so nothing was saved')
            refuse_public_github_remote(url,fail_closed=not (own and isinstance(target,str) and target.lower()==own))
        # Checked once, here. A process that can rewrite this checkout's git config mid-run could equally
        # change this code or the state files, so re-checking before each push would add no protection.
        self.check_head()
    def _destinations(self):
        """Every URL git will push to. For a named remote git reports its push URLs with insteadOf/pushInsteadOf
        rewrites applied; a folder destination has none to check, provided no rewrite could redirect it."""
        if self._named:
            # A remote helper (remote.<name>.vcs) decides for itself where the data goes.
            if subprocess.run(['git','config','--get',f'remote.{self.remote}.vcs'],cwd=self.repo,capture_output=True,env=push_config_env()).returncode!=1:
                raise ValueError(f'The {self.remote!r} remote uses a git remote helper, so its destination can\'t be confirmed; nothing was saved')
            return [u for u in self._git('remote','get-url','--push','--all',self.remote).splitlines() if u.strip()]
        # git rewrites even local paths with url.*.insteadOf/pushInsteadOf (an empty prefix matches
        # everything), which could send the "folder" anywhere. Don't try to replicate git's matching:
        # with any rewrite rule configured, require a named remote, whose push URLs git reports.
        if self._has_url_rewrites():
            raise ValueError('Git URL rewrite rules are configured, so a folder destination could be redirected; publish delivery state to a named remote instead')
        return []
    def _has_url_rewrites(self):
        r=subprocess.run(['git','config','--get-regexp',r'^url\..*\.(push)?insteadof$'],cwd=self.repo,capture_output=True,env=push_config_env())
        if r.returncode not in (0,1): raise RuntimeError('Could not read git URL rewrite settings')
        return r.returncode==0                       # 1 means no rules configured
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
            raise RuntimeError(f'State publication failed ({git_failure(exc)}); no automatic recovery or force push') from exc
        finally: self._publishing=False
        self.check_head()
