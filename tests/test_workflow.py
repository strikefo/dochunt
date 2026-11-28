from pathlib import Path
import pytest


def test_workflow_contract():
    w=Path('.github/workflows/document-hunt.yml').read_text()
    assert "17 * * * *" in w and 'timeout-minutes: 30' in w
    assert 'cancel-in-progress: false' in w and 'push:' not in w
    assert w.index('due-check')<w.index('pip install')
    assert 'contents: write' in w and 'retention-days: 7' in w
    assert 'default: preview' in w
    assert '${{ inputs.' not in w

def test_ci_is_credential_free():
    w=Path('.github/workflows/ci.yml').read_text()
    assert 'pull_request:' in w and 'push:' not in w
    assert 'contents: read' in w and 'secrets.' not in w
    assert 'pytest' in w and 'compileall' in w and 'pip wheel' in w
    assert 'preview --synthetic' in w and 'examples/*.toml' in w


def test_ci_cancels_only_superseded_checks_for_the_same_pr():
    w=Path('.github/workflows/ci.yml').read_text()
    assert 'group: ${{ github.workflow }}-${{ github.event.pull_request.number }}' in w
    assert 'cancel-in-progress: true' in w
    assert 'cancel-in-progress: false' in Path('.github/workflows/document-hunt.yml').read_text()


def test_pinned_actions_deadline_and_private_artifacts():
    import re
    w=Path('.github/workflows/document-hunt.yml').read_text()
    assert 'github.event.repository.private' in w
    assert 'DOCHUNT_DEADLINE' in w and 'timeout "$remaining"' in w
    for path in Path('.github/workflows').glob('*.yml'):
        for line in path.read_text().splitlines():
            if 'uses:' in line: assert re.search(r'actions/[a-z-]+@[a-f0-9]{40}',line)


def step(text,name):
    import re
    parts=re.split(r'\n      - (?=name:|uses:)',text)
    matches=[p for p in parts if p.startswith(f'name: {name}\n')]
    assert len(matches)==1, name
    return matches[0]


def test_agent_steps_get_ai_keys_but_no_git_or_mail_credentials():
    text=Path('.github/workflows/document-hunt.yml').read_text()
    checkout=[p for p in text.split('\n      - ') if p.startswith('uses: actions/checkout')][0]
    assert 'persist-credentials: false' in checkout
    for name in ('Research','Paid AI preview (research only, no email)'):
        body=step(text,name)
        assert 'OPENAI_API_KEY' in body and 'ANTHROPIC_API_KEY' in body
        for forbidden in ('GIT_CONFIG','github.token','bot-token','RESEND_API_KEY','SMTP_'):
            assert forbidden not in body, (name,forbidden)
    for name in ('Deliver with synchronous state publication','Explicit selected-preview test or resend'):
        body=step(text,name)
        assert "steps.bot-token.outputs.token || github.token" in body
        assert 'OPENAI_API_KEY' not in body and 'ANTHROPIC_API_KEY' not in body


def test_agent_cli_versions_are_pinned():
    text=Path('.github/workflows/document-hunt.yml').read_text()
    assert 'package=@openai/codex@0.163.0' in text and 'package=@anthropic-ai/claude-code@2.1.299' in text
    assert 'npm install --global "$PACKAGE"' in text


def test_mail_readiness_step_precedes_ai_and_has_only_mail_secrets():
    from pathlib import Path
    text=Path('.github/workflows/document-hunt.yml').read_text()
    readiness=step(text,'Delivery credential readiness before paid research')
    assert 'dochunt delivery-readiness' in readiness and 'SMTP_PASSWORD' in readiness
    assert 'OPENAI_API_KEY' not in readiness and 'ANTHROPIC_API_KEY' not in readiness
    research=step(text,'Research')
    assert 'SMTP_PASSWORD' not in research and 'RESEND_API_KEY' not in research


def test_readiness_cli_validates_header_without_ai(tmp_path,monkeypatch):
    from dochunt.cli import main
    from pathlib import Path
    path=tmp_path/'newsroom.toml'
    text=Path('examples/education.toml').read_text().replace('briefing@example.invalid','bríefing@example.invalid')
    # Use the actual configured sender spelling, independent of example address.
    import tomllib
    original=tomllib.loads(text)['email']['sender']
    text=text.replace(original,'bríefing@example.invalid'); path.write_text(text)
    monkeypatch.setenv('RESEND_API_KEY','synthetic')
    with pytest.raises(SystemExit): main(['delivery-readiness','--config',str(path),'--state-dir',str(tmp_path/'state')])


def _eligibility_script():
    w=Path('.github/workflows/document-hunt.yml').read_text()
    block=w.split('- name: Eligibility before dependency installation')[1].split('      - name: Install')[0]
    run=block.split('run: |\n',1)[1]
    return '\n'.join(line[10:] for line in run.splitlines())


def test_external_timer_dispatch_gets_scheduled_checks_never_manual(tmp_path):
    import os, subprocess
    w=Path('.github/workflows/document-hunt.yml').read_text()
    assert 'options: [preview, paid-ai-preview, send-preview-test, deliver, resend, scheduled]' in w
    bin_dir=tmp_path/'bin'; bin_dir.mkdir()
    stub=bin_dir/'python'; stub.write_text('#!/bin/sh\necho "$@" > "$ARGS_FILE"\n'); stub.chmod(0o755)
    script=_eligibility_script()
    def run(mode):
        args=tmp_path/f'args-{mode or "cron"}'; out=tmp_path/f'out-{mode or "cron"}'
        env={**os.environ,'PATH':f'{bin_dir}:{os.environ["PATH"]}','MODE':mode,'ARGS_FILE':str(args),'GITHUB_OUTPUT':str(out)}
        subprocess.run(['bash','-e','-c',script],env=env,check=True)
        return args.read_text().strip() if args.exists() else None, out.read_text() if out.exists() else ''
    assert run('scheduled')[0]=='-m dochunt.cli due-check'
    assert run('')[0]=='-m dochunt.cli due-check'
    assert run('deliver')[0]=='-m dochunt.cli due-check --manual'
    args,out=run('paid-ai-preview')
    assert args is None and 'due=false' in out


def test_undue_scheduled_dispatch_skips_installation():
    w=Path('.github/workflows/document-hunt.yml').read_text()
    install=w.split('      - name: Install\n')[1].split('\n')[0].strip()
    assert install=="if: steps.due.outputs.due == 'true' || (github.event_name == 'workflow_dispatch' && github.event.inputs.mode != 'scheduled')"


def test_local_preview_needs_an_explicit_kind(tmp_path, capsys):
    from dochunt.cli import main
    hunt = Path('.github/workflows/document-hunt.yml').read_text()
    assert 'dochunt preview --paid --output-dir output' in hunt   # the workflow's billed preview says so explicitly
    with pytest.raises(SystemExit) as stop:   # neither --synthetic nor --paid: nothing runs, nothing is billed
        main(['preview', '--config', 'examples/education.toml', '--output-dir', str(tmp_path / 'out')])
    assert stop.value.code == 2 and 'one of the arguments --synthetic --paid is required' in capsys.readouterr().err
    assert not (tmp_path / 'out').exists()


def test_preview_summary_step_and_test_send_defaults():
    w=Path('.github/workflows/document-hunt.yml').read_text()
    step=w[w.index('- name: Show the preview on the run page'):]
    step=step[:step.index('\n      - ')]
    assert 'secrets.' not in step and '>> "$GITHUB_STEP_SUMMARY"' in step
    assert "mode == 'preview'" in step and "mode == 'paid-ai-preview'" in step
    assert w.index('dochunt preview --paid')<w.index('Show the preview on the run page')
    # Only a test gets a default attempt ID; resend still needs an explicit one.
    assert '--attempt-id "${ATTEMPT_ID:-test-$GITHUB_RUN_ID}"' in w
    assert 'dochunt resend --issue-dir selected-preview --attempt-id "$ATTEMPT_ID"' in w
