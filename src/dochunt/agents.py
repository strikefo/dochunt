"""Run the research agent (Codex CLI or Claude Code) headless, isolated from repository secrets.

The agent gets web search and nothing else that matters: a scrubbed environment
holding only its own AI key, an empty temporary working directory, and no email
credentials, recipients or Git tokens. It returns JSON matching the issue schema.
"""
from dataclasses import dataclass, field
from importlib.resources import files
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time

SCHEMA_TEXT = files('dochunt').joinpath('schema', 'issue.schema.json').read_text(encoding='utf-8')
PASS_ENV = ('PATH', 'LANG', 'LC_ALL', 'TZ', 'SSL_CERT_FILE', 'SSL_CERT_DIR', 'NODE_EXTRA_CA_CERTS')


class AgentError(RuntimeError):
    """The agent did not return usable JSON. Message is safe to print."""


@dataclass
class AgentResult:
    data: object
    raw: str
    seconds: float
    usage: dict = field(default_factory=dict)


def _env(home, extra):
    env = {k: os.environ[k] for k in PASS_ENV if k in os.environ}
    env.update(HOME=str(home), XDG_CONFIG_HOME=str(Path(home) / '.config'), **extra)
    return env


def _json_from_text(text):
    text = (text or '').strip()
    if text.startswith('```'):
        text = text.split('\n', 1)[1] if '\n' in text else ''
        text = text.rsplit('```', 1)[0]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find('{'), text.rfind('}')
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
    raise AgentError('The agent did not return a JSON object')


def run_process(cmd, *, cwd, env, input, capture_output, text, timeout):
    """subprocess.run, but a timeout kills the whole process group.

    The npm-installed CLIs are launchers that spawn the real agent; killing only the
    launcher would leave billed research running after DocHunt gave up on it.
    """
    process = subprocess.Popen(cmd, cwd=cwd, env=env, text=text, start_new_session=True,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    previous = {}

    def interrupted(signum, frame):
        raise KeyboardInterrupt(f'signal {signum}')
    try:
        # The agent runs in its own session, so it would not see the workflow's or operator's
        # SIGTERM/SIGINT. Turn them into an exception here and stop the whole group below.
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                previous[sig] = signal.signal(sig, interrupted)
            except ValueError:  # not the main thread
                pass
        stdout, stderr = process.communicate(input, timeout=timeout)
    except BaseException:
        _stop_group(process)
        raise
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    return subprocess.CompletedProcess(cmd, process.returncode, stdout, stderr)


def _group_alive(pgid):
    try:
        os.killpg(pgid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _stop_group(process, grace=5.0):
    """SIGTERM the whole group, then SIGKILL whatever is left; the launcher exiting is not enough."""
    pgid = process.pid
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    end = time.monotonic() + grace
    while time.monotonic() < end and _group_alive(pgid):
        try:
            process.wait(timeout=0.1)
        except subprocess.TimeoutExpired:
            pass
    if _group_alive(pgid):
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    try:
        process.communicate(timeout=5)
    except (subprocess.TimeoutExpired, ValueError, OSError):
        pass


def _run(runner, cmd, *, env, cwd, timeout, input=None):
    try:
        return runner(cmd, cwd=cwd, env=env, input=input, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise AgentError(f'The agent did not finish within {timeout // 60} minutes') from exc
    except FileNotFoundError as exc:
        raise AgentError(f'Agent CLI not installed: {cmd[0]}') from exc


# Every Codex capability that could read local files is switched off: no shell, exec, apps,
# plugins, browser or computer use (newsroom.toml and data/ hold recipients). The read-only
# sandbox stays as a backstop. `code_mode_host` stays ON: Codex 0.163 routes tool calls,
# including web search, through it, and disabling it silently removes web access. Its code
# runs in a bare JS runtime with no fs, Deno or fetch (checked with a canary-file test).
# Names are checked against the pinned CLI's `codex features list` in tests.
CODEX_DISABLED_FEATURES = ('shell_tool', 'unified_exec', 'apps', 'plugins',
                           'browser_use', 'browser_use_external', 'in_app_browser', 'computer_use')


def codex_command(config, *, run_dir, schema, out):
    disabled = [arg for name in CODEX_DISABLED_FEATURES for arg in ('--disable', name)]
    # --disable is a top-level option and goes before the exec subcommand. Web search is the
    # `web_search` config setting: the top-level --search flag belongs to the interactive app and
    # never reaches `codex exec` (a live run had no web access with it).
    return ['codex', *disabled, 'exec', '--ephemeral', '--ignore-user-config', '--ignore-rules', '--skip-git-repo-check',
            '--sandbox', 'read-only', '--json', '-C', str(run_dir), '-m', config.model,
            *codex_config_overrides(config), '--output-schema', str(schema), '-o', str(out), '-']


def codex_config_overrides(config):
    effort = {'max': 'xhigh'}.get(config.agent.reasoning_effort, config.agent.reasoning_effort)
    return ['-c', 'web_search="live"', '-c', f'model_reasoning_effort="{effort}"']


def claude_command(config):
    return ['claude', '-p', '--bare', '--output-format', 'json', '--json-schema', SCHEMA_TEXT,
            '--tools', 'WebSearch,WebFetch', '--allowedTools', 'WebSearch,WebFetch', '--permission-mode', 'dontAsk',
            '--strict-mcp-config', '--no-session-persistence', '--model', config.model,
            '--effort', config.agent.reasoning_effort, '--max-budget-usd', f'{config.agent.max_budget_usd:.2f}']


def run_codex(config, prompt, *, key, runner, workdir, timeout):
    home = workdir / 'home'
    codex_home = workdir / 'codex-home'
    run_dir = workdir / 'run'
    for d in (home, codex_home, run_dir):
        d.mkdir(mode=0o700)
    env = _env(home, {'CODEX_HOME': str(codex_home)})
    login = _run(runner, ['codex', 'login', '--with-api-key'], env=env, cwd=run_dir, timeout=60, input=key)
    if login.returncode != 0:
        raise AgentError('Codex could not sign in with the OpenAI API key')
    schema = workdir / 'issue.schema.json'
    schema.write_text(SCHEMA_TEXT, encoding='utf-8')
    out = workdir / 'last-message.json'
    cmd = codex_command(config, run_dir=run_dir, schema=schema, out=out)
    result = _run(runner, cmd, env=env, cwd=run_dir, timeout=timeout, input=prompt)
    usage = {}
    for line in (result.stdout or '').splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and isinstance(event.get('usage'), dict):
            for k, v in event['usage'].items():
                if isinstance(v, int):
                    usage[k] = usage.get(k, 0) + v
        if isinstance(event, dict) and event.get('type') == 'item.completed' and \
                isinstance(event.get('item'), dict) and event['item'].get('type') == 'web_search':
            usage['web_searches'] = usage.get('web_searches', 0) + 1  # zero means the agent had no web access
    if result.returncode != 0 or not out.exists():
        raise AgentError(f'Codex exited with status {result.returncode}')
    raw = out.read_text(encoding='utf-8')
    return _json_from_text(raw), raw, usage


def run_claude(config, prompt, *, key, runner, workdir, timeout):
    home = workdir / 'home'
    run_dir = workdir / 'run'
    for d in (home, run_dir):
        d.mkdir(mode=0o700)
    env = _env(home, {'ANTHROPIC_API_KEY': key, 'DISABLE_AUTOUPDATER': '1', 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1'})
    cmd = claude_command(config)
    result = _run(runner, cmd, env=env, cwd=run_dir, timeout=timeout, input=prompt)
    try:
        envelope = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise AgentError(f'Claude Code exited with status {result.returncode} without a result') from exc
    if not isinstance(envelope, dict):
        raise AgentError('Claude Code returned an unexpected result')
    usage = {k: v for k, v in (envelope.get('usage') or {}).items() if isinstance(v, int)}
    if isinstance(envelope.get('total_cost_usd'), (int, float)):
        usage['cost_usd'] = round(float(envelope['total_cost_usd']), 4)
    if envelope.get('is_error') or result.returncode != 0:
        subtype = envelope.get('subtype') or 'error'
        if 'budget' in str(subtype):
            raise AgentError(f'Claude Code stopped at the ${config.agent.max_budget_usd:.2f} run budget')
        raise AgentError(f'Claude Code did not finish ({subtype})')
    structured = envelope.get('structured_output')
    if isinstance(structured, dict):
        return structured, json.dumps(structured, ensure_ascii=False), usage
    raw = envelope.get('result') if isinstance(envelope.get('result'), str) else ''
    return _json_from_text(raw), raw, usage


RUNNERS = {'codex': run_codex, 'claude': run_claude}


def run_agent(config, prompt, *, timeout=None, runner=run_process, environ=None):
    """Run the configured agent once. Raises AgentError with a printable message."""
    from .config import RUNTIMES
    environ = os.environ if environ is None else environ
    key = environ.get(RUNTIMES[config.agent.runtime], '')
    if not key:
        raise AgentError(f'Missing {RUNTIMES[config.agent.runtime]} repository secret')
    workdir = Path(tempfile.mkdtemp(prefix='dochunt-agent-'))
    start = time.monotonic()
    try:
        data, raw, usage = RUNNERS[config.agent.runtime](
            config, prompt, key=key, runner=runner, workdir=workdir, timeout=int(timeout or config.agent.timeout_minutes * 60))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return AgentResult(data, raw, round(time.monotonic() - start, 1), usage)
