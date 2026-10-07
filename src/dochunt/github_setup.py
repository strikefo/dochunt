"""Optional owner-operated GitHub setup. Never print CLI diagnostics or secret values."""
from dataclasses import asdict, dataclass
from pathlib import Path
import json
import re
import subprocess
import tomllib
from urllib.parse import quote
from .config import NewsroomConfig, config_from_data


def serialize_config(config: NewsroomConfig) -> str:
    data=asdict(config)
    config_from_data(data)  # Exactly the loader's validation, including types and limits.
    def literal(value):
        if type(value) is bool: return str(value).lower()
        if type(value) is int: return str(value)
        if isinstance(value,str): return json.dumps(value,ensure_ascii=False)
        if isinstance(value,list): return '['+', '.join(literal(v) for v in value)+']'
        raise ValueError('Unsupported TOML value')
    lines=['# Credentials belong only in GitHub Actions repository secrets.']
    for section,record in data.items():
        if section=='sources': continue
        lines.extend(['',f'[{section}]',*[f'{k} = {literal(v)}' for k,v in record.items()]])
    for record in data['sources']:
        lines.extend(['','[[sources]]',*[f'{k} = {literal(v)}' for k,v in record.items()]])
    return '\n'.join(lines)+'\n'


def run_command(args,*,cwd,input=None):
    try:
        result=subprocess.run(args,cwd=cwd,input=input,text=True,capture_output=True,timeout=40,check=False)
        if result.returncode: raise ValueError('Git or GitHub CLI operation failed; inspect account and repository permissions outside the wizard.')
        return result.stdout.strip()
    except (OSError,subprocess.TimeoutExpired):
        raise ValueError('Git and authenticated GitHub CLI are required; operation unavailable.') from None

@dataclass(frozen=True)
class RepoTarget:
    path: Path
    repository: str
    branch: str
    head: str
    remote: str
    protected: bool
    secrets: frozenset[str]
    config_text: str

    @property
    def url(self): return 'https://github.com/'+self.repository

@dataclass(frozen=True)
class SaveResult:
    pushed: bool
    message: str
    manual_toml: str = ''
    verified_head: str = ''

@dataclass(frozen=True)
class SecretUpdateResult:
    name: str
    success: bool
    message: str


def inspect_target(path: Path,*,runner=run_command) -> RepoTarget:
    path=Path(path).resolve()
    def run(args): return runner(args,cwd=path)
    try:
        if Path(run(['git','rev-parse','--show-toplevel'])).resolve()!=path: raise ValueError()
        config_path=path/'newsroom.toml'
        run(['git','ls-files','--error-unmatch','--','newsroom.toml'])
        if config_path.is_symlink() or not config_path.is_file(): raise ValueError()
        if run(['git','status','--porcelain','--untracked-files=all']): raise ValueError()
        branch=run(['git','symbolic-ref','--short','HEAD'])
        if not branch or branch.startswith('-') or any(ch in branch for ch in '\r\n'): raise ValueError()
        head=run(['git','rev-parse','HEAD'])
        if not re.fullmatch('[0-9a-f]{40,64}',head): raise ValueError()
        remote=run(['git','remote','get-url','origin'])
        if run(['git','remote','get-url','--push','origin'])!=remote: raise ValueError()
        match=re.fullmatch(r'(?:git@github\.com:|https://github\.com/)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?',remote)
        if not match: raise ValueError()
        repository=match[1]
        run(['gh','auth','status','--hostname','github.com'])
        login=json.loads(run(['gh','api','user']))['login']
        repo=json.loads(run(['gh','api',f'repos/{repository}']))
        if repo.get('full_name')!=repository or repo.get('private') is not True or repository.split('/')[0].lower()!=login.lower() or repo.get('permissions',{}).get('push') is not True: raise ValueError()
        if repo.get('default_branch')!=branch: raise ValueError()
        tip=run(['git','ls-remote','--exit-code','origin',f'refs/heads/{branch}']).split()
        if tip!=[head,f'refs/heads/{branch}']: raise ValueError()
        encoded=quote(branch,safe='')
        protected=json.loads(run(['gh','api',f'repos/{repository}/branches/{encoded}'])).get('protected')
        rules=json.loads(run(['gh','api',f'repos/{repository}/rules/branches/{encoded}']))
        if type(protected) is not bool or not isinstance(rules,list): raise ValueError()
        secrets=json.loads(run(['gh','secret','list','--repo',repository,'--json','name']))
        if not isinstance(secrets,list) or any(not isinstance(v.get('name'),str) for v in secrets): raise ValueError()
        return RepoTarget(path,repository,branch,head,remote,protected or bool(rules),frozenset(v['name'] for v in secrets),config_path.read_text(encoding='utf-8'))
    except (ValueError,KeyError,TypeError,RuntimeError,OSError):
        raise ValueError('Setup requires a clean, current default-branch checkout of your own private GitHub repository, authenticated CLI and readable branch/secret permissions. No changes made.') from None


def recheck(target,runner):
    current=inspect_target(target.path,runner=runner)
    if (current.repository,current.branch,current.head,current.remote,current.config_text)!=(target.repository,target.branch,target.head,target.remote,target.config_text):
        raise ValueError('Repository changed since confirmation. Restart setup and review changes.')
    return current


def save_config(target: RepoTarget,config: NewsroomConfig,*,runner=run_command) -> SaveResult:
    text=serialize_config(config)
    try:
        current=recheck(target,runner)
        if current.protected: raise ValueError('Branch rules require a manual pull request.')
        if text==current.config_text: return SaveResult(True,'Configuration already matches the verified default branch.',verified_head=current.head)
        (target.path/'newsroom.toml').write_text(text,encoding='utf-8')
        runner(['git','add','--','newsroom.toml'],cwd=target.path)
        runner(['git','-c','core.hooksPath=/dev/null','commit','--only','-m','Configure newsroom settings','--','newsroom.toml'],cwd=target.path)
        committed_head=runner(['git','rev-parse','HEAD'],cwd=target.path)
        if runner(['git','rev-parse','HEAD^'],cwd=target.path)!=target.head or runner(['git','diff','--name-only',target.head,committed_head],cwd=target.path)!='newsroom.toml': raise ValueError('Unexpected commit contents')
        runner(['git','-c','core.hooksPath=/dev/null','push','origin',f'HEAD:refs/heads/{target.branch}'],cwd=target.path)
        verify_saved_target(target,config,committed_head,runner=runner)
        return SaveResult(True,'Configuration committed, pushed and verified on the default branch. Inspect Actions for validation.',verified_head=committed_head)
    except (ValueError,OSError,RuntimeError):
        return SaveResult(False,'Configuration was not confirmed pushed. Download the settings below and use the GitHub file editor to propose a pull request. If a local commit exists, inspect it before retrying; no force push or policy change was attempted.',text)


def verify_saved_target(target,config,expected_head,*,runner=run_command):
    current=inspect_target(target.path,runner=runner)
    if not expected_head or (current.repository,current.branch,current.remote,current.head,current.config_text)!=(target.repository,target.branch,target.remote,expected_head,serialize_config(config)):
        raise ValueError('Unexpected repository change after save; no secret updates allowed.')
    return current


def require_secrets(config,configured):
    if not set(config.secret_names.values()) <= set(configured):
        raise ValueError('Selected providers need configured repository secrets before schedule activation.')

ALLOWED_SECRETS={'FIRECRAWL_API_KEY','OPENAI_API_KEY','ANTHROPIC_API_KEY','RESEND_API_KEY','SMTP_USERNAME','SMTP_PASSWORD'}

def set_secrets(target: RepoTarget,values: dict,*,runner=run_command) -> list[SecretUpdateResult]:
    if not isinstance(values,dict) or set(values)-ALLOWED_SECRETS or any(not isinstance(v,str) or len(v)>64000 for v in values.values()): raise ValueError('Invalid secret fields')
    supplied={k:v for k,v in values.items() if v}
    if not supplied: return []
    try:
        current=recheck(target,runner)
        if config_from_data(tomllib.loads(current.config_text)).schedule.enabled: raise ValueError('Disable scheduling before replacing secrets.')
    except ValueError:
        return [SecretUpdateResult(name,False,'Repository changed or permissions unavailable. Restart setup; ask the repository administrator if needed.') for name in supplied]
    results=[]
    for name,value in supplied.items():
        try:
            recheck(target,runner)
            runner(['gh','secret','set',name,'--repo',target.repository],cwd=target.path,input=value)
            results.append(SecretUpdateResult(name,True,'Repository secret updated.'))
        except (ValueError,OSError,RuntimeError):
            results.append(SecretUpdateResult(name,False,'Secret update failed or is uncertain; retry via GitHub Settings or ask an authorized administrator.'))
    return results
