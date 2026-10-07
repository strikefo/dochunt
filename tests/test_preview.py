"""Previews never send mail or change state; the synthetic preview runs the real engine."""
from datetime import date

import pytest

from dochunt.cli import main, preview
from support import config, provider as fictional_provider


def test_preview_never_sends_or_updates_history(tmp_path):
    history = tmp_path / 'history.jsonl'
    history.write_text('')
    c = config()
    p = fictional_provider(c)
    p.send = lambda *a: pytest.fail('preview sent mail')
    preview(c, tmp_path / 'output', provider=p, history_path=history, local_date=date(2026, 10, 2))
    assert history.read_text() == ''
    assert sorted(x.name for x in (tmp_path / 'output').iterdir()) == [
        'analysed-documents.json', 'engine-result.json', 'issue.json', 'preview-manifest.json',
        'preview.html', 'preview.txt', 'raw', 'sources.json']


def test_explicit_synthetic_preview_is_fictional_and_offline(tmp_path, monkeypatch):
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **k: pytest.fail('network used'))
    assert main(['preview', '--config', 'examples/education.toml', '--output-dir', str(tmp_path), '--synthetic']) == 0
    text = (tmp_path / 'preview.txt').read_text()
    assert 'Example County' in text and 'Send the request to: records@publicworks.example' in text


def test_provider_failure_writes_no_issue(tmp_path):
    c = config()
    p = fictional_provider(c, outputs=[])  # the fixture transport is empty: the analysis call fails
    with pytest.raises(Exception):
        preview(c, tmp_path / 'output', provider=p, local_date=date(2026, 10, 2))
    assert not (tmp_path / 'output' / 'issue.json').exists()
