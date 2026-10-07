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
    assert 'pull_request:' in w and 'push:' in w
    assert 'contents: read' in w and 'secrets.' not in w
    assert 'pytest' in w and 'compileall' in w and 'pip wheel' in w


def test_pinned_actions_deadline_and_private_artifacts():
    import re
    w=Path('.github/workflows/document-hunt.yml').read_text()
    assert 'github.event.repository.private' in w
    assert 'DOCHUNT_DEADLINE' in w and 'timeout "$remaining"' in w
    for path in Path('.github/workflows').glob('*.yml'):
        for line in path.read_text().splitlines():
            if 'uses:' in line: assert re.search(r'actions/[a-z-]+@[a-f0-9]{40}',line)


def test_mail_readiness_step_precedes_ai_and_has_only_mail_secrets():
    from pathlib import Path
    text=Path('.github/workflows/document-hunt.yml').read_text()
    readiness=text.split('      - name: Delivery credential readiness before paid research')[1].split('      - name: Research and review')[0]
    assert 'dochunt delivery-readiness' in readiness and 'SMTP_PASSWORD' in readiness
    assert 'OPENAI_API_KEY' not in readiness and 'ANTHROPIC_API_KEY' not in readiness
    research=text.split('      - name: Research and review')[1].split('      - name: Deliver with')[0]
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
