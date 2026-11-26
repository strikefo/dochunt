"""Agent adapters, exercised with a fake subprocess runner (no CLI, no network, no keys)."""
import json
import subprocess
from pathlib import Path
import pytest
from support import config, sample_data
from dochunt.agents import AgentError, run_agent


class Runner:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def __call__(self, cmd, *, cwd, env, input, capture_output, text, timeout):
        self.calls.append({'cmd': cmd, 'cwd': cwd, 'env': env, 'input': input, 'timeout': timeout})
        return self.handler(cmd, Path(cwd), env, input)


def done(stdout='', code=0):
    return subprocess.CompletedProcess([], code, stdout, '')


ENV = {'PATH': '/usr/bin', 'OPENAI_API_KEY': 'sk-openai-fixture', 'ANTHROPIC_API_KEY': 'sk-ant-fixture',
       'RESEND_API_KEY': 're-secret', 'SMTP_PASSWORD': 'smtp-secret', 'GITHUB_TOKEN': 'gh-secret',
       'GIT_CONFIG_VALUE_0': 'token-url', 'HOME': '/home/runner'}


def codex_handler(answer):
    def handle(cmd, cwd, env, input):
        if cmd[:2] == ['codex', 'login']:
            return done()
        out = Path(cmd[cmd.index('-o') + 1])
        out.write_text(answer if isinstance(answer, str) else json.dumps(answer))
        events = '\n'.join([json.dumps({'type': 'item.completed', 'item': {'type': 'web_search', 'query': 'q'}}),
                             json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 900, 'output_tokens': 120}}), 'not json'])
        return done(events)
    return handle


def test_codex_runs_isolated_with_only_its_key():
    c = config('examples/military.toml')
    runner = Runner(codex_handler(sample_data()))
    result = run_agent(c, 'PROMPT', runner=runner, environ=ENV)
    assert result.data == sample_data() and result.usage == {'input_tokens': 900, 'output_tokens': 120, 'web_searches': 1}
    login, run = runner.calls
    assert login['input'] == 'sk-openai-fixture' and 'sk-openai-fixture' not in ' '.join(login['cmd'])
    assert run['input'] == 'PROMPT'
    for call in runner.calls:
        env = call['env']
        assert set(env) <= {'PATH', 'HOME', 'XDG_CONFIG_HOME', 'CODEX_HOME', 'LANG', 'LC_ALL', 'TZ'}
        assert not any(v in json.dumps(env) for v in ('re-secret', 'smtp-secret', 'gh-secret', 'token-url', 'sk-ant-fixture'))
        assert env['HOME'] != '/home/runner' and 'dochunt-agent-' in str(call['cwd'])
    cmd = run['cmd']
    exec_at = cmd.index('exec')
    assert cmd[0] == 'codex' and '--search' not in cmd  # --search never reaches `codex exec`
    assert cmd[cmd.index('web_search="live"') - 1] == '-c' and cmd.index('web_search="live"') > exec_at
    disabled = {cmd[i + 1] for i in range(exec_at) if cmd[i] == '--disable'}
    assert {'shell_tool', 'unified_exec', 'computer_use', 'browser_use'} <= disabled
    assert 'code_mode_host' not in disabled  # Codex routes web search through it
    for flag in ('--ephemeral', '--ignore-user-config', '--ignore-rules', '--skip-git-repo-check', '--output-schema'):
        assert flag in cmd
    assert cmd[cmd.index('--sandbox') + 1] == 'read-only' and cmd[cmd.index('-m') + 1] == 'gpt-6.1-sol'
    assert 'model_reasoning_effort="high"' in cmd
    assert not Path(run['cwd']).exists()  # the temporary workspace is removed


def test_codex_failures_are_readable_and_secret_free():
    c = config('examples/military.toml')
    with pytest.raises(AgentError, match='sign in'):
        run_agent(c, 'P', runner=Runner(lambda *a: done(code=1)), environ=ENV)
    with pytest.raises(AgentError, match='Missing OPENAI_API_KEY'):
        run_agent(c, 'P', runner=Runner(codex_handler({})), environ={'PATH': '/usr/bin'})
    def no_output(cmd, cwd, env, input):
        return done() if cmd[1] == 'login' else done(code=2)
    with pytest.raises(AgentError, match='status 2') as error:
        run_agent(c, 'P', runner=Runner(no_output), environ=ENV)
    assert 'sk-' not in str(error.value)


def test_codex_timeout_and_missing_cli():
    c = config('examples/military.toml')
    def slow(cmd, cwd, env, input):
        if cmd[1] == 'login':
            return done()
        raise subprocess.TimeoutExpired(cmd, 60)
    with pytest.raises(AgentError, match='did not finish within 2 minutes'):
        run_agent(c, 'P', timeout=120, runner=Runner(slow), environ=ENV)
    def missing(cmd, cwd, env, input):
        raise FileNotFoundError(cmd[0])
    with pytest.raises(AgentError, match='not installed: codex'):
        run_agent(c, 'P', runner=Runner(missing), environ=ENV)


def claude_handler(envelope, code=0):
    return lambda cmd, cwd, env, input: done(json.dumps(envelope) if not isinstance(envelope, str) else envelope, code)


def test_claude_uses_web_tools_only_with_a_budget_and_schema():
    c = config('examples/education.toml')
    runner = Runner(claude_handler({'type': 'result', 'subtype': 'success', 'is_error': False, 'result': '',
                                    'structured_output': sample_data(), 'total_cost_usd': 1.2345,
                                    'usage': {'input_tokens': 5000, 'output_tokens': 700, 'server_tool_use': {}}}))
    result = run_agent(c, 'PROMPT', runner=runner, environ=ENV)
    assert result.data == sample_data() and result.usage == {'input_tokens': 5000, 'output_tokens': 700, 'cost_usd': 1.2345}
    call, = runner.calls
    cmd, env = call['cmd'], call['env']
    assert cmd[cmd.index('--tools') + 1] == 'WebSearch,WebFetch' and cmd[cmd.index('--allowedTools') + 1] == 'WebSearch,WebFetch'
    assert cmd[cmd.index('--max-budget-usd') + 1] == '5.00' and cmd[cmd.index('--model') + 1] == 'claude-opus-5-5'
    schema = json.loads(cmd[cmd.index('--json-schema') + 1])
    assert schema['title'] == 'DocHunt issue' and '$schema' not in schema   # the CLI rejects the 2020-12 declaration
    for flag in ('--safe-mode', '--disable-slash-commands', '--strict-mcp-config', '--no-session-persistence'):
        assert flag in cmd
    assert cmd[cmd.index('--permission-mode') + 1] == 'dontAsk' and '--bare' not in cmd   # --bare drops the web tools
    assert env['ANTHROPIC_API_KEY'] == 'sk-ant-fixture' and 'OPENAI_API_KEY' not in env
    assert env['CLAUDE_CONFIG_DIR'] == str(Path(env['HOME']) / '.claude') and env['HOME'] != ENV['HOME']
    assert not any(v in json.dumps(env) for v in ('re-secret', 'smtp-secret', 'gh-secret', 'token-url'))
    assert call['input'] == 'PROMPT' and 'sk-ant-fixture' not in ' '.join(cmd)


def test_claude_falls_back_to_json_in_the_result_text():
    c = config('examples/education.toml')
    text = 'Here it is:\n```json\n' + json.dumps(sample_data()) + '\n```'
    result = run_agent(c, 'P', runner=Runner(claude_handler({'is_error': False, 'result': text})), environ=ENV)
    assert result.data == sample_data()


@pytest.mark.parametrize('envelope,code,message', [
    ({'is_error': True, 'subtype': 'error_max_budget_usd'}, 1, r'stopped at the \$5.00 run budget'),
    ({'is_error': True, 'subtype': 'error_max_turns'}, 1, 'did not finish'),
    ('not json', 1, 'without a result'),
    ({'is_error': False, 'result': 'No JSON here'}, 0, 'did not return a JSON object'),
])
def test_claude_errors(envelope, code, message):
    with pytest.raises(AgentError, match=message):
        run_agent(config('examples/education.toml'), 'P', runner=Runner(claude_handler(envelope, code)), environ=ENV)


def test_pinned_codex_accepts_the_exact_command(tmp_path):
    """Argument parsing only: --help stops before any sign-in or model call."""
    import shutil
    from dochunt.agents import codex_command
    if not shutil.which('codex'):
        pytest.skip('codex CLI not installed (CI installs the pinned version)')
    cmd = codex_command(config('examples/military.toml'), run_dir=tmp_path, schema=tmp_path / 's.json', out=tmp_path / 'o.json')
    result = subprocess.run(cmd + ['--help'], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    assert result.returncode == 0, result.stderr


def test_claude_startup_error_stays_status_only():
    """CLI stderr is never copied into AgentError, which is printed and saved in check reports."""
    runner = Runner(lambda cmd, cwd, env, input: subprocess.CompletedProcess(
        [], 1, '', 'Error: bad argument sk-ant-fixture PROMPT TEXT\n'))
    with pytest.raises(AgentError) as err:
        run_agent(config('examples/education.toml'), 'P', runner=runner, environ=ENV)
    assert str(err.value) == 'Claude Code exited with status 1 without a result'


SENTINEL = 'dochunt-loopback-sentinel'


def run_pinned_claude(tmp_path, cmd):
    """Run the pinned Claude CLI against a loopback stand-in for the API that records each request and
    rejects it with a known error. Dummy key, temporary home and config directory, no retries."""
    import os
    import shutil
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    if not shutil.which('claude'):
        pytest.skip('claude CLI not installed (CI installs the pinned version)')
    seen = []

    class API(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length') or 0))
            seen.append(self.path)
            body = json.dumps({'type': 'error', 'error': {'type': 'authentication_error', 'message': SENTINEL}}).encode()
            self.send_response(401)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(('127.0.0.1', 0), API)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    (tmp_path / 'home').mkdir()
    env = {'PATH': os.environ['PATH'], 'HOME': str(tmp_path / 'home'), 'ANTHROPIC_API_KEY': 'dummy-not-a-key',
           'CLAUDE_CONFIG_DIR': str(tmp_path / 'home' / '.claude'),
           'ANTHROPIC_BASE_URL': f'http://127.0.0.1:{server.server_port}', 'CLAUDE_CODE_MAX_RETRIES': '0',
           'DISABLE_AUTOUPDATER': '1', 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1'}
    try:
        result = subprocess.run(cmd, input='hi', capture_output=True, text=True, timeout=120, cwd=tmp_path, env=env)
    finally:
        server.shutdown()
        server.server_close()
    return result, seen


def test_pinned_claude_accepts_the_exact_command(tmp_path):
    """The CLI must pass its own argument and schema checks, send the model request, and report the
    stand-in API's error in its JSON result envelope."""
    from dochunt.agents import claude_command
    result, seen = run_pinned_claude(tmp_path, claude_command(config('examples/education.toml')))
    from urllib.parse import urlsplit
    assert '/v1/messages' in [urlsplit(path).path for path in seen], (seen, result.stderr[:300])   # not count_tokens
    envelope = json.loads(result.stdout)
    assert envelope['is_error'] is True and SENTINEL in json.dumps(envelope), result.stdout[:500]


def test_pinned_claude_gives_the_agent_web_tools_and_nothing_else(tmp_path):
    """The CLI's init event lists the tools the agent will have."""
    from dochunt.agents import claude_command
    cmd = claude_command(config('examples/education.toml'))
    cmd[cmd.index('--output-format') + 1:cmd.index('--output-format') + 2] = ['stream-json', '--verbose']
    result, seen = run_pinned_claude(tmp_path, cmd)
    events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
    init, = [e for e in events if e.get('type') == 'system' and e.get('subtype') == 'init']
    assert set(init['tools']) == {'StructuredOutput', 'WebSearch', 'WebFetch'}, init['tools']
    assert init['mcp_servers'] == [] and not init.get('skills') and init['permissionMode'] == 'dontAsk'
    assert init['apiKeySource'] == 'ANTHROPIC_API_KEY'


def test_ci_installs_the_same_cli_versions_as_the_hunt_workflow():
    import re
    hunt = Path('.github/workflows/document-hunt.yml').read_text()
    ci = Path('.github/workflows/ci.yml').read_text()
    pins = set(re.findall(r'@(?:openai/codex|anthropic-ai/claude-code)@[0-9.]+', hunt))
    assert len(pins) == 2 and all(pin in ci for pin in pins)
    setup = Path('docs/setup.md').read_text()   # local paid previews use the same pinned CLIs
    assert all(pin in setup for pin in pins), pins


def test_timeout_kills_the_whole_process_tree(tmp_path):
    import os
    import sys
    import time
    from dochunt.agents import run_process
    pid_file = tmp_path / 'child.pid'
    script = (f"import subprocess,sys,time\n"
              f"c=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\n"
              f"open({str(pid_file)!r},'w').write(str(c.pid))\n"
              f"time.sleep(60)\n")
    with pytest.raises(subprocess.TimeoutExpired):
        run_process([sys.executable, '-c', script], cwd=tmp_path, env=dict(os.environ), input='',
                    capture_output=True, text=True, timeout=2)
    child = int(pid_file.read_text())
    for _ in range(50):
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        pytest.fail('agent child process survived the timeout')


def test_pinned_codex_accepts_the_web_search_and_effort_settings(tmp_path):
    """Config values are only validated when a session is built; prompt-input builds one offline."""
    import os
    import shutil
    from dochunt.agents import CODEX_DISABLED_FEATURES, codex_config_overrides
    if not shutil.which('codex'):
        pytest.skip('codex CLI not installed (CI installs the pinned version)')
    disabled = [a for name in CODEX_DISABLED_FEATURES for a in ('--disable', name)]
    cmd = ['codex', *disabled, *codex_config_overrides(config('examples/military.toml')), 'debug', 'prompt-input', 'hi']
    env = {**os.environ, 'CODEX_HOME': str(tmp_path)}
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, cwd=tmp_path, env=env)
    assert result.returncode == 0, result.stderr
    bad = subprocess.run(['codex', '-c', 'web_search="nonsense"', 'debug', 'prompt-input', 'hi'],
                         capture_output=True, text=True, timeout=60, cwd=tmp_path, env=env)
    assert bad.returncode != 0 and 'live' in bad.stderr  # proves the setting is really validated


def test_disabled_codex_features_exist_in_the_pinned_cli():
    import shutil
    from dochunt.agents import CODEX_DISABLED_FEATURES
    if not shutil.which('codex'):
        pytest.skip('codex CLI not installed (CI installs the pinned version)')
    listed = subprocess.run(['codex', 'features', 'list'], capture_output=True, text=True, timeout=60, cwd='/').stdout
    names = {line.split()[0] for line in listed.splitlines() if line.strip()}
    assert set(CODEX_DISABLED_FEATURES) <= names


def test_termination_signal_stops_the_agent_process_tree(tmp_path):
    import os
    import signal
    import sys
    import threading
    import time
    from dochunt.agents import run_process
    pid_file = tmp_path / 'child.pid'
    script = (f"import subprocess,sys,time\n"
              f"c=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\n"
              f"open({str(pid_file)!r},'w').write(str(c.pid))\n"
              f"time.sleep(60)\n")
    timer = threading.Timer(1.5, lambda: os.kill(os.getpid(), signal.SIGTERM))
    timer.start()
    before = signal.getsignal(signal.SIGTERM)
    with pytest.raises(KeyboardInterrupt):
        run_process([sys.executable, '-c', script], cwd=tmp_path, env=dict(os.environ), input='',
                    capture_output=True, text=True, timeout=30)
    assert signal.getsignal(signal.SIGTERM) is before
    child = int(pid_file.read_text())
    for _ in range(50):
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        pytest.fail('agent child process survived the termination signal')


def test_a_descendant_that_ignores_sigterm_is_killed(tmp_path):
    import os
    import sys
    import time
    from dochunt.agents import run_process
    pid_file = tmp_path / 'child.pid'
    child_code = "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"
    script = (f"import subprocess,sys,time\n"
              f"c=subprocess.Popen([sys.executable,'-c',{child_code!r}],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
              f"open({str(pid_file)!r},'w').write(str(c.pid))\n"
              f"time.sleep(60)\n")
    with pytest.raises(subprocess.TimeoutExpired):
        run_process([sys.executable, '-c', script], cwd=tmp_path, env=dict(os.environ), input='',
                    capture_output=True, text=True, timeout=2)
    child = int(pid_file.read_text())
    for _ in range(80):
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        pytest.fail('a SIGTERM-ignoring descendant survived')
