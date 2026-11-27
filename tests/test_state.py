import json
import os
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
    (tmp_path/'data').mkdir(); (tmp_path/'data/state.json').write_text('{}\n'); (tmp_path/'data/leads.jsonl').write_text('')
    git('add','data'); git('commit','-m','seed')
    (tmp_path/'unrelated.py').write_text('private code'); git('add','unrelated.py')
    s=StateStore(tmp_path/'data',repo=tmp_path); s.save({'attempts':{},'completed':{},'leads':[]})
    assert git('diff','--cached','--name-only').strip()=='unrelated.py'
    assert 'unrelated.py' not in git('show','--format=','--name-only','HEAD')


def test_concurrent_commit_after_precheck_preserved_and_no_send(tmp_path,monkeypatch):
    from test_delivery import prepared, Fake
    from dochunt.delivery import send
    import json
    def git(*a): return subprocess.run(['git',*a],cwd=tmp_path,check=True,capture_output=True,text=True).stdout
    git('init'); git('config','user.email','test@example.invalid'); git('config','user.name','Test')
    (tmp_path/'data').mkdir(); (tmp_path/'data/state.json').write_text('{}\n'); (tmp_path/'data/leads.jsonl').write_text('')
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


def test_publication_failures_name_the_git_command_and_hide_credentials():
    from dochunt.state import git_failure
    known=subprocess.CalledProcessError(128,['git','push','origin','abc:refs/heads/main'],output='',
        stderr="remote: Permission to o/r.git denied to bot.\nfatal: unable to access 'https://x-access-token:ghs_SECRETvalue@github.com/o/r/': 403")
    text=git_failure(known)
    assert text.startswith('git push exited with status 128') and 'lacks permission' in text
    assert 'SECRET' not in text and 'x-access-token' not in text
    unknown=subprocess.CalledProcessError(1,['git','push'],output='',stderr=(
        "remote: odd failure while fetching https://storage.example/obj?X-Amz-Signature=SIGSECRET123&exp=1\n"
        "remote: debug token=abcdefghijklmnopqrstuvwxyzSECRET42\nerror: something new happened"))
    text=git_failure(unknown)
    assert 'odd failure' in text and 'something new happened' in text
    assert 'SIGSECRET' not in text and 'SECRET42' not in text and 'storage.example' not in text


def test_rejected_push_reports_why(tmp_path):
    def git(*a,cwd=tmp_path): return subprocess.run(['git',*a],cwd=cwd,check=True,capture_output=True,text=True).stdout
    remote=tmp_path/'remote.git'; git('init','--bare','--initial-branch=main',str(remote))
    seed=tmp_path/'seed'; git('clone',str(remote),str(seed))
    for k,v in (('user.email','t@example.invalid'),('user.name','T')): git('config',k,v,cwd=seed)
    (seed/'data').mkdir(); (seed/'data/state.json').write_text('{}\n'); (seed/'data/leads.jsonl').write_text('')
    git('add','data',cwd=seed); git('commit','-m','seed',cwd=seed); git('push','origin','main',cwd=seed)
    hook=remote/'hooks'/'pre-receive'; hook.write_text('#!/bin/sh\necho "blocked by test hook" >&2\nexit 1\n'); hook.chmod(0o700)
    from dochunt.state import PublishedStateStore
    store=PublishedStateStore(seed/'data',repo=seed)
    with pytest.raises(RuntimeError, match='git push exited .*server-side hook declined'):
        store.save({'attempts':{}})


def test_git_diagnostics_separate_http_rejections_from_network_failures_and_stay_fast(monkeypatch):
    from dochunt import state
    from dochunt.state import git_failure
    http=subprocess.CalledProcessError(128,['git','push'],output='',stderr="fatal: unable to access 'https://github.com/o/r/': The requested URL returned error: 403")
    assert 'reached' not in git_failure(http) and 'lacks permission' in git_failure(http)
    tls=subprocess.CalledProcessError(128,['git','push'],output='',stderr="fatal: unable to access 'https://github.com/o/r/': SSL certificate problem: unable to get local issuer certificate")
    assert 'SSL certificate problem' in git_failure(tls)
    dns=subprocess.CalledProcessError(128,['git','push'],output='',stderr="fatal: unable to access 'https://github.com/o/r/': Could not resolve host: github.com")
    assert 'could not be reached' in git_failure(dns)
    long=subprocess.CalledProcessError(1,['git','push'],output='',stderr='remote: '+'a'*20000+'\nerror: short one')
    seen=[]; real=state._scrub
    monkeypatch.setattr(state,'_scrub',lambda line: seen.append(line) or real(line))
    text=git_failure(long)
    assert seen==['error: short one']  # the 20,000-character line is never scrubbed
    assert '<long line omitted>' in text and 'short one' in text


@pytest.mark.parametrize('url,repo', [
    ('https://github.com/owner/repo.git', 'owner/repo'),
    ('https://x-access-token:abc@github.com/owner/repo', 'owner/repo'),
    ('git@github.com:owner/repo.git', 'owner/repo'),
    ('ssh://git@github.com/owner/repo.git', 'owner/repo'),
    ('https://github.com:443/owner/repo.git', 'owner/repo'),
    ('ssh://git@ssh.github.com:443/owner/repo.git', 'owner/repo'),
    ('git@ssh.github.com:owner/repo', 'owner/repo'),
    ('https://GitHub.com/owner/repo', 'owner/repo'),
    ('git@gitlab.example:owner/repo.git', None),
    ('https://github.com/owner/./repo.git', '<unrecognized GitHub path>'),
    ('https://github.com/owner/repo/extra', '<unrecognized GitHub path>'),
    ('https://github.com/owner/.github', 'owner/.github'),
    ('git@evil.example:x@github.com:owner/repo', None),         # git connects to evil.example
    ('evil.example:git@github.com:owner/repo', None),
    ('bad user@github.com:owner/repo', None),
    ('helper::https://github.com/owner/repo', None),            # remote helper
    ('git+ssh://github.com/owner/repo', None),
    ('http://github.com/owner/repo', None),
    ('https://ssh.github.com/owner/repo', None),
    ('HTTPS://github.com/owner/repo', None),
    ('https://github.com/owner/project.wiki.git', '<unrecognized GitHub path>'),   # a wiki is public with its project
    ('git@github.com:owner/Project.Wiki', '<unrecognized GitHub path>'),                    # git would look for a helper named "HTTPS"
    ('Ssh://git@github.com/owner/repo', None),
    ('https://github.com/owner/a..b.git', 'owner/a..b'),
    ('https://github.com/owner/../repo', '<unrecognized GitHub path>'),
    ('/tmp/remote.git', None),
    ('https://gitlab.example/owner/repo.git', None),
])
def test_github_remote_parsing(url, repo):
    from dochunt.state import github_repository
    assert github_repository(url) == repo


def test_delivery_state_is_never_published_to_a_public_repository():
    import io, json
    from urllib.error import HTTPError, URLError
    from dochunt.state import refuse_public_github_remote
    class Response(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): pass
    def answers(data): return lambda req, timeout: Response(json.dumps(data).encode())
    with pytest.raises(RuntimeError, match='public repository owner/repo'):
        refuse_public_github_remote('https://github.com/owner/repo.git', opener=answers({'private': False}))
    refuse_public_github_remote('https://github.com/owner/repo.git', opener=answers({'private': True}))
    def missing(req, timeout): raise HTTPError(req.full_url, 404, 'Not Found', {}, None)   # private repos are invisible anonymously
    refuse_public_github_remote('https://github.com/owner/repo.git', opener=missing)
    def offline(req, timeout): raise URLError('offline')
    def limited(req, timeout): raise HTTPError(req.full_url, 403, 'rate limited', {}, None)
    for unclear in (offline, limited, answers(['not an object'])):
        with pytest.raises(RuntimeError, match='Could not confirm'):
            refuse_public_github_remote('https://github.com/owner/repo.git', opener=unclear)
        refuse_public_github_remote('https://github.com/owner/repo.git', opener=unclear, fail_closed=False)
    refuse_public_github_remote('/tmp/remote.git', opener=lambda *a, **k: pytest.fail('no request for non-GitHub remotes'))
    with pytest.raises(RuntimeError, match='unrecognized path'):
        refuse_public_github_remote('https://github.com/owner/./repo.git', fail_closed=False, opener=lambda *a, **k: pytest.fail('no request'))


def test_every_push_destination_is_checked_and_plain_paths_still_work(tmp_path, monkeypatch):
    # The test decides when rewrite rules exist: ignore the machine's global and system git settings.
    (tmp_path / 'empty.gitconfig').write_text('')
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', str(tmp_path / 'empty.gitconfig')); monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')
    for name in ('GIT_CONFIG_COUNT', 'GIT_CONFIG_PARAMETERS', 'GIT_CONFIG'): monkeypatch.delenv(name, raising=False)
    from dochunt import state as state_module
    from dochunt.state import PublishedStateStore
    def git(*a, cwd=tmp_path): return subprocess.run(['git', *a], cwd=cwd, check=True, capture_output=True, text=True).stdout
    remote = tmp_path / 'remote.git'; git('init', '--bare', '--initial-branch=main', str(remote))
    seed = tmp_path / 'seed'; git('clone', str(remote), str(seed))
    for k, v in (('user.email', 't@example.invalid'), ('user.name', 'T')): git('config', k, v, cwd=seed)
    (seed / 'data').mkdir(); (seed / 'data/state.json').write_text('{}\n'); (seed / 'data/leads.jsonl').write_text('')
    git('add', 'data', cwd=seed); git('commit', '-m', 'seed', cwd=seed); git('push', 'origin', 'main', cwd=seed)
    checked = []
    monkeypatch.setattr(state_module, 'refuse_public_github_remote', lambda url, **k: checked.append((url, k['fail_closed'])))
    monkeypatch.delenv('GITHUB_ACTIONS', raising=False)
    PublishedStateStore(seed / 'data', repo=seed, remote=str(remote))            # a local folder, not a remote name
    assert checked == []                                                          # nothing on GitHub to check
    with pytest.raises(ValueError, match='named git remote'):
        PublishedStateStore(seed / 'data', repo=seed, remote='https://github.com/someone/repo.git')
    (tmp_path / 'elsewhere').mkdir(); monkeypatch.chdir(tmp_path / 'elsewhere')
    PublishedStateStore(seed / 'data', repo=seed, remote=os.path.relpath(remote, seed))   # relative to the checkout
    git('config', f'url.https://github.com/someone/public.git.pushInsteadOf', str(remote), cwd=seed)
    with pytest.raises(ValueError, match='rewrite rules'):
        PublishedStateStore(seed / 'data', repo=seed, remote=str(remote))
    git('config', '--unset', f'url.https://github.com/someone/public.git.pushInsteadOf', cwd=seed)
    spaced = tmp_path / 'some folder'; spaced.mkdir()
    git('config', f'url.{tmp_path}/other mirror.git.pushInsteadOf', str(spaced), cwd=seed)   # key and value with spaces
    with pytest.raises(ValueError, match='rewrite rules'):
        PublishedStateStore(seed / 'data', repo=seed, remote=str(spaced))
    git('config', '--unset', f'url.{tmp_path}/other mirror.git.pushInsteadOf', cwd=seed)
    git('config', 'url.https://github.com/someone/public.git.insteadOf', '', cwd=seed)     # empty prefix matches everything
    with pytest.raises(ValueError, match='rewrite rules'):
        PublishedStateStore(seed / 'data', repo=seed, remote=str(remote))
    git('config', 'url.https://github.com/someone/public.git.pushInsteadOf', '/unrelated/', cwd=seed)   # any rule at all
    git('config', '--unset', 'url.https://github.com/someone/public.git.insteadOf', cwd=seed)
    with pytest.raises(ValueError, match='rewrite rules'):
        PublishedStateStore(seed / 'data', repo=seed, remote=str(remote))
    git('config', '--unset', 'url.https://github.com/someone/public.git.pushInsteadOf', cwd=seed)
    git('clone', '--bare', str(remote), str(seed / 'host:path'))                 # ssh-shaped name of a real local mirror
    store = PublishedStateStore(seed / 'data', repo=seed, remote='host:path')      # git would read "host:path" as ssh
    assert store.remote == str((seed / 'host:path').resolve())
    git('remote', 'set-url', '--add', '--push', 'origin', 'https://github.com/newsroom/private-copy.git', cwd=seed)
    git('remote', 'set-url', '--add', '--push', 'origin', 'https://github.com/someone/public-mirror.git', cwd=seed)
    monkeypatch.setenv('GITHUB_ACTIONS', 'true'); monkeypatch.setenv('GITHUB_REPOSITORY', 'newsroom/private-copy')
    event = tmp_path / 'event.json'; monkeypatch.setenv('GITHUB_EVENT_PATH', str(event))
    event.write_text(json.dumps({'repository': {'full_name': 'newsroom/private-copy', 'private': False}}))
    PublishedStateStore(seed / 'data', repo=seed)          # the name alone earns no leniency
    assert checked[-2:] == [('https://github.com/newsroom/private-copy.git', True), ('https://github.com/someone/public-mirror.git', True)]
    checked.clear()
    event.write_text(json.dumps({'repository': {'full_name': 'newsroom/private-copy', 'private': True}}))
    PublishedStateStore(seed / 'data', repo=seed)
    # Only the workflow's own (private-by-guarantee) repository gets the lenient check.
    assert checked == [('https://github.com/newsroom/private-copy.git', False), ('https://github.com/someone/public-mirror.git', True)]


@pytest.mark.parametrize('url,local', [
    ('/srv/git/state.git', True), ('../mirror.git', True), ('file:///srv/git/state.git', True), ('./a:b', True),
    ('git@github.com:owner/repo.git', False), ('github-work:owner/repo', False), ('host:path', False),
    ('https://github.com/owner/repo.git', False), ('ssh://git@gitlab.example/owner/repo.git', False),
])
def test_local_git_path_follows_gits_rule(url, local):
    from dochunt.state import is_local_git_path
    assert is_local_git_path(url) is local


@pytest.mark.parametrize('url', ['github-work:newsroom/state.git', 'git@gitlab.example:newsroom/state.git', 'https://git.example/newsroom/state.git',
                                 'helper::https://github.com/newsroom/state', 'git@evil.example:x@github.com:newsroom/state'])
def test_unverifiable_network_destinations_are_refused(tmp_path, monkeypatch, url):
    from dochunt.state import PublishedStateStore
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', str(tmp_path / 'none')); monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')
    for name in ('GIT_CONFIG_COUNT', 'GIT_CONFIG_PARAMETERS', 'GIT_CONFIG'): monkeypatch.delenv(name, raising=False)
    def git(*a): subprocess.run(['git', *a], cwd=tmp_path, check=True, capture_output=True)
    git('init', '--initial-branch=main'); git('-c', 'user.name=T', '-c', 'user.email=t@example.invalid', 'commit', '--allow-empty', '-m', 'seed')
    git('remote', 'add', 'origin', url)
    with pytest.raises(ValueError, match='only to a private GitHub repository'):
        PublishedStateStore(tmp_path / 'data', repo=tmp_path)


def test_remote_helpers_are_refused(tmp_path, monkeypatch):
    from dochunt.state import PublishedStateStore
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', str(tmp_path / 'none')); monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')
    for name in ('GIT_CONFIG_COUNT', 'GIT_CONFIG_PARAMETERS', 'GIT_CONFIG'): monkeypatch.delenv(name, raising=False)
    def git(*a): subprocess.run(['git', *a], cwd=tmp_path, check=True, capture_output=True)
    git('init', '--initial-branch=main'); git('-c', 'user.name=T', '-c', 'user.email=t@example.invalid', 'commit', '--allow-empty', '-m', 'seed')
    git('remote', 'add', 'origin', str(tmp_path / 'looks-local.git')); git('config', 'remote.origin.vcs', 'elsewhere')
    with pytest.raises(ValueError, match='remote helper'):
        PublishedStateStore(tmp_path / 'data', repo=tmp_path)
    (tmp_path / 'other.gitconfig').write_text('')
    monkeypatch.setenv('GIT_CONFIG', str(tmp_path / 'other.gitconfig'))   # git push ignores it; so must the check
    with pytest.raises(ValueError, match='remote helper'):
        PublishedStateStore(tmp_path / 'data', repo=tmp_path)



@pytest.mark.parametrize('payload,expected', [
    ({'repository': {'full_name': 'News/Own', 'private': True}}, 'news/own'),
    ({'repository': {'full_name': 'news/own', 'private': False}}, ''),
    ({'repository': {'full_name': 'news/own', 'private': 'true'}}, ''),     # only a real boolean counts
    ({'repository': {'full_name': 'news/other', 'private': True}}, ''),     # must be this run's repository
    ({}, ''), ([], ''),
])
def test_actions_privacy_evidence_comes_from_the_event(tmp_path, monkeypatch, payload, expected):
    from dochunt.state import actions_private_repository
    event = tmp_path / 'event.json'; event.write_text(json.dumps(payload))
    monkeypatch.setenv('GITHUB_ACTIONS', 'true'); monkeypatch.setenv('GITHUB_REPOSITORY', 'news/own'); monkeypatch.setenv('GITHUB_EVENT_PATH', str(event))
    assert actions_private_repository() == expected
    monkeypatch.setenv('GITHUB_ACTIONS', 'false')
    assert actions_private_repository() == ''


def test_tracker_survives_unicode_line_separators_in_lead_text(tmp_path):
    """json.dumps(ensure_ascii=False) keeps U+2028 inside a string; reading must not split on it."""
    store = StateStore(tmp_path, git=False)
    rows = [{'lead_id': 'a', 'headline': 'Budget\u2028cut\u2029and\x85more', 'source_key': 'k'}, {'lead_id': 'b', 'source_key': 'j'}]
    store.save({'attempts': {}, 'leads': rows})
    assert store.read()['leads'] == rows
