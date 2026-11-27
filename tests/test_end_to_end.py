"""Full offline flows: paid preview -> test send -> scheduled delivery -> next day -> resend."""
from datetime import datetime, timezone
import json
import pytest
from support import FakeAgent, NoCalls, config, no_fetch, sample_data
from test_delivery import Fake
from test_runtime import repos  # noqa: F401  (pytest fixture)
from dochunt.artifacts import load_preview
from dochunt.delivery import prepare, send
from dochunt.hunt import preview
from dochunt.runtime import run


@pytest.mark.parametrize('example', ['examples/military.toml', 'examples/education.toml'])
def test_test_send_then_delivery_then_next_day_and_resend(repos, tmp_path, example):  # noqa: F811
    _, store = repos
    c = config(example)
    clock = datetime(2026, 10, 2, 14, tzinfo=timezone.utc)
    t = Fake()
    local = clock.astimezone(__import__('zoneinfo').ZoneInfo(c.schedule.timezone)).date()
    preview(c, tmp_path / 'preview', local_date=local, synthetic=False, agent=FakeAgent(), fetch=no_fetch)
    i, r = load_preview(tmp_path / 'preview', c, require_paid=True)
    send(prepare(i, r, c, recipient=c.email.recipients[0], attempt_id='test1'), store, t)
    assert not store.read()['completed'] and not store.read()['leads']  # a test send records nothing

    receipt = run(c, store, tmp_path / 'normal', agent=FakeAgent(), fetch=no_fetch, transport=t, manual=True, now_utc=clock)
    assert receipt.state.value == 'accepted' and len(t.calls) == 2 and len(store.read()['leads']) == 3
    assert not run(c, store, tmp_path / 'repeat', agent=NoCalls(), transport=t, manual=True, now_utc=clock).due

    # Next day the agent repeats yesterday's leads twice: they are dropped, and an honest no-lead email goes out.
    agent = FakeAgent(sample_data(), sample_data())
    run(c, store, tmp_path / 'next', agent=agent, fetch=no_fetch, transport=t, manual=True, now_utc=clock.replace(day=3))
    assert t.calls[-1].rendered['subject'] == 'No actionable records leads qualified today'
    report = json.loads((tmp_path / 'next' / 'check-report.json').read_text())
    assert len(report['dropped']) == 3 and 'already sent' in agent.prompts[1]

    # An explicit operator resend has its own attempt but preserves the reviewed payload.
    resend = prepare(i, r, c, attempt_id='operator-1')
    send(resend, store, t, repeat=True)   # what `dochunt resend` passes
    send(resend, store, t, repeat=True)
    assert len(t.calls) == 4 and t.calls[-1].rendered == t.calls[0].rendered and t.calls[-1].attempt_key != t.calls[1].attempt_key


def test_interrupted_send_recovers_without_new_research(repos, tmp_path):  # noqa: F811
    _, store = repos
    c = config()
    clock = datetime(2026, 10, 2, 14, tzinfo=timezone.utc)

    class Lost(Fake):
        def send(self, p):
            raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        run(c, store, tmp_path / 'out', agent=FakeAgent(), fetch=no_fetch, transport=Lost(), manual=True, now_utc=clock)
    t = Fake()
    run(c, store, tmp_path / 'out', agent=NoCalls(), transport=t, manual=True, now_utc=clock)
    assert len(t.calls) == 1


def test_direct_github_modes_and_safe_inputs():
    from pathlib import Path
    w = Path('.github/workflows/document-hunt.yml').read_text()
    for mode in ['paid-ai-preview', 'send-preview-test', 'resend']:
        assert mode in w
    assert 'download-preview' in w and 'actions: read' in w
    assert '${{ github.event.inputs.recipient }}' not in w.split('run:')[-1]
    assert w.count('group: document-hunt-state') == 1
