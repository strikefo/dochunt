import pytest
from html import escape
import json
from pathlib import Path
from datetime import date
from support import config, FakeAgent, no_fetch
from dochunt.hunt import preview
from dochunt.summary import preview_summary

D = date(2026, 10, 2)
HOSTILE = '</pre><img src=x onerror=alert(1)> **bold** [click](https://evil.example) <script>x</script> & $1,200'


def test_paid_preview_summary_shows_the_email_and_how_to_test_it(tmp_path):
    preview(config(), tmp_path, local_date=D, synthetic=False, agent=FakeAgent(), fetch=no_fetch)
    out = preview_summary(tmp_path, '123456')
    assert out.startswith('<h2>Paid AI preview</h2>')
    assert 'preview run ID <code>123456</code>' in out and 'send-preview-test' in out
    assert 'lead(s) in the email' in out
    assert escape((tmp_path / 'preview.txt').read_text()) in out


def test_synthetic_preview_summary_says_it_is_fictional_and_offers_no_send(tmp_path):
    preview(config(), tmp_path, local_date=D, synthetic=True)
    out = preview_summary(tmp_path, '123456')
    assert 'Fictional preview' in out and 'send-preview-test' not in out


def test_agent_text_cannot_escape_the_summary_markup(tmp_path):
    """The email text and subject come from an agent that read untrusted pages."""
    (tmp_path / 'preview.txt').write_text(HOSTILE + '\n' + '`' * 5)
    (tmp_path / 'issue.json').write_text(json.dumps({'issue': {'subject': '<b>' + HOSTILE, 'leads': [{}]}}))
    (tmp_path / 'preview-manifest.json').write_text(json.dumps({'mode': 'paid-ai-preview'}))
    (tmp_path / 'check-report.json').write_text(json.dumps({'attempts': [
        {'seconds': 90, 'usage': {'cost_usd': 1.25}}, {'seconds': 30, 'usage': {'cost_usd': 0.5}}],
        'dropped': [{'headline': 'x'}], 'warnings': ['w1', 'w2']}))
    out = preview_summary(tmp_path, '<run>')
    for raw in ('<img', '<script', '<b>', '</pre><', '<run>'):
        assert raw not in out
    assert out.count('<pre>') == 1 and out.count('</pre>') == 1 and out.rstrip().endswith('</pre>')
    assert '&lt;/pre&gt;&lt;img' in out and '&amp; $1,200' in out
    assert '1 lead(s) in the email; 1 dropped by the checks; 2 warning(s); 2.0 min of agent time; about $1.75' in out


def test_missing_report_files_still_show_the_email(tmp_path):
    (tmp_path / 'preview.txt').write_text('Just the email')
    out = preview_summary(tmp_path)
    assert 'Just the email' in out and 'Preview (type unknown)' in out
    assert 'Fictional' not in out and 'lead(s)' not in out


def test_summary_cli_needs_no_config(tmp_path, capsys, monkeypatch):
    from dochunt.cli import main
    (tmp_path / 'preview.txt').write_text('Hello')
    monkeypatch.chdir(tmp_path)
    assert main(['summary', '--output-dir', str(tmp_path), '--run-id', '9']) == 0
    assert '<pre>Hello</pre>' in capsys.readouterr().out


def test_each_part_is_its_own_html_block_so_blank_lines_cannot_end_the_pre_early(tmp_path):
    (tmp_path / 'preview.txt').write_text('First paragraph\n\n**two** [link](https://evil.example)\n\nthree')
    out = preview_summary(tmp_path)
    head, body = out.split('<pre>')
    assert head.endswith('\n\n')   # <pre> starts a new block instead of continuing the <p> above it
    assert body.count('</pre>') == 1 and body.rstrip().endswith('</pre>')


def test_usage_is_flagged_as_partial_when_a_repair_left_no_usage(tmp_path):
    (tmp_path / 'preview.txt').write_text('x')
    (tmp_path / 'check-report.json').write_text(json.dumps({'attempts': [
        {'attempt': 1, 'seconds': 60, 'usage': {'cost_usd': 1.0}}, {'attempt': 2, 'error': 'Claude Code stopped at the $5 run budget'}]}))
    assert 'cover only attempts that finished' in preview_summary(tmp_path)


def test_a_huge_hostile_preview_still_fits_githubs_summary_limit(tmp_path):
    (tmp_path / 'preview.txt').write_text("'" * 1_000_000)
    (tmp_path / 'issue.json').write_text(json.dumps({'issue': {'subject': "'" * 100_000}}))
    out = preview_summary(tmp_path, '1')
    assert len(out.encode('utf-8')) < 1024 * 1024 and 'cut short here' in out


def test_a_skipped_repair_leaves_usage_complete(tmp_path):
    (tmp_path / 'preview.txt').write_text('x')
    (tmp_path / 'check-report.json').write_text(json.dumps({'attempts': [
        {'attempt': 1, 'seconds': 60, 'usage': {'cost_usd': 1.0}},
        {'attempt': 2, 'skipped': True, 'error': 'Skipped repair: not enough time before the run deadline'}]}))
    out = preview_summary(tmp_path)
    assert 'about $1.00' in out and 'cover only attempts' not in out


def test_send_hint_names_the_artifact_window_and_who_a_blank_recipient_is(tmp_path):
    preview(config(), tmp_path, local_date=D, synthetic=False, agent=FakeAgent(), fetch=no_fetch)
    out = preview_summary(tmp_path, '42')
    assert 'within 7 days' in out and 'whoever it belongs to' in out and 'to yourself' not in out


@pytest.mark.parametrize('manifest', ['{"mode": []}', '{"mode": {"x": 1}}', '[]', '"paid-ai-preview"', 'not json'])
def test_malformed_report_files_still_show_the_email(tmp_path, manifest):
    (tmp_path / 'preview.txt').write_text('The email')
    (tmp_path / 'preview-manifest.json').write_text(manifest)
    (tmp_path / 'check-report.json').write_text('[1, 2]')
    (tmp_path / 'issue.json').write_text('{"issue": []}')
    out = preview_summary(tmp_path, '1')
    assert '<pre>The email</pre>' in out and 'Preview (type unknown)' in out


@pytest.mark.parametrize('report', [{'warnings': 1}, {'dropped': 1}, {'attempts': 1}, {'attempts': [{'seconds': 'x', 'usage': 5}]},
                                    {'attempts': [{'seconds': float('nan')}]}, {'attempts': [{'usage': {'cost_usd': True}}]}])
def test_wrongly_typed_report_fields_still_show_the_email(tmp_path, report):
    (tmp_path / 'preview.txt').write_text('The email')
    (tmp_path / 'check-report.json').write_text(json.dumps(report))
    assert '<pre>The email</pre>' in preview_summary(tmp_path, '1')


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), -5, True, '60'])
def test_a_malformed_attempt_does_not_spoil_valid_totals(tmp_path, bad):
    (tmp_path / 'preview.txt').write_text('x')
    (tmp_path / 'check-report.json').write_text(json.dumps({'attempts': [
        {'seconds': 120, 'usage': {'cost_usd': 1.5}}, {'seconds': bad, 'usage': {'cost_usd': bad}}]}))
    out = preview_summary(tmp_path)
    assert '2.0 min of agent time' in out and 'about $1.50' in out


def test_a_clean_report_says_zero_dropped_and_a_missing_one_says_nothing(tmp_path):
    (tmp_path / 'preview.txt').write_text('x')
    assert 'dropped' not in preview_summary(tmp_path)
    (tmp_path / 'check-report.json').write_text(json.dumps({'dropped': []}))
    assert '0 dropped by the checks' in preview_summary(tmp_path)
