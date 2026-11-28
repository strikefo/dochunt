from datetime import date
import json
import pytest
from support import AgentError, FakeAgent, NoCalls, config, no_fetch, sample_data
from dochunt.hunt import preview, research
from dochunt.models import IssueFormatError

D = date(2026, 10, 2)


def test_clean_answer_needs_one_agent_call():
    agent = FakeAgent()
    issue, report = research(config(), D, [], agent=agent, fetch=no_fetch)
    assert len(agent.prompts) == 1 and len(issue.leads) == 3
    assert report['attempts'][0]['problems'] == [] and report['runtime'] == 'claude'


def test_problems_trigger_one_repair_with_the_exact_problems():
    broken = sample_data()
    broken['leads'][0]['get_records']['verified_at_url'] = ''
    agent = FakeAgent(broken, sample_data())
    issue, report = research(config(), D, [], agent=agent, fetch=no_fetch)
    assert len(agent.prompts) == 2 and len(issue.leads) == 3
    assert 'name the official page where the contact or route was verified' in agent.prompts[1]
    assert json.dumps(broken['leads'][0]['headline'])[1:-1] in agent.prompts[1]
    assert report['attempts'][1]['problems'] == []


def test_failed_repair_still_sends_the_valid_leads_from_the_first_answer():
    broken = sample_data()
    broken['leads'][0]['get_records']['verified_at_url'] = ''
    agent = FakeAgent(broken, AgentError('Claude Code did not finish (error_max_turns)'))
    issue, report = research(config(), D, [], agent=agent, fetch=no_fetch)
    assert len(issue.leads) == 2 and report['dropped'] and 'did not finish' in report['attempts'][1]['error']


def test_a_repair_that_is_still_broken_drops_only_the_broken_leads():
    broken = sample_data()
    broken['leads'][2]['get_records']['contact']['url'] = ''
    agent = FakeAgent(broken, broken)
    issue, _ = research(config(), D, [], agent=agent, fetch=no_fetch)
    assert len(agent.prompts) == 2 and len(issue.leads) == 2


def test_agent_failure_on_the_first_call_writes_no_preview(tmp_path):
    with pytest.raises(AgentError):
        preview(config(), tmp_path, local_date=D, synthetic=False, agent=FakeAgent(AgentError('Missing ANTHROPIC_API_KEY repository secret')), fetch=no_fetch)
    assert not (tmp_path / 'issue.json').exists()


def test_unusable_output_twice_fails_the_run():
    with pytest.raises(IssueFormatError):
        research(config(), D, [], agent=FakeAgent('nonsense', 'still nonsense'), fetch=no_fetch)


def test_repair_is_skipped_near_the_workflow_deadline(monkeypatch):
    import time
    broken = sample_data()
    broken['leads'][0]['get_records']['verified_at_url'] = ''
    agent = FakeAgent(broken)
    monkeypatch.setenv('DOCHUNT_DEADLINE', str(int(time.time()) + 200))
    issue, report = research(config(), D, [], agent=agent, fetch=no_fetch)
    assert len(agent.prompts) == 1 and len(issue.leads) == 2
    assert 'Skipped repair' in report['attempts'][1]['error'] and report['attempts'][1]['skipped'] is True
    assert agent.timeouts[0] < 200


def test_agent_time_limit_comes_from_config_without_a_deadline(monkeypatch):
    monkeypatch.delenv('DOCHUNT_DEADLINE', raising=False)
    agent = FakeAgent()
    research(config(), D, [], agent=agent, fetch=no_fetch)
    assert agent.timeouts == [18 * 60]


def test_synthetic_preview_is_offline(tmp_path):
    preview(config(), tmp_path, local_date=D, synthetic=True, agent=NoCalls(), fetch=NoCalls())
    report = json.loads((tmp_path / 'check-report.json').read_text())
    assert report['runtime'] == 'synthetic' and 'Example County' in (tmp_path / 'preview.txt').read_text()
    assert not (tmp_path / 'raw').exists()


def test_paid_preview_keeps_prompt_raw_output_and_report(tmp_path):
    preview(config(), tmp_path, local_date=D, synthetic=False, agent=FakeAgent(), fetch=lambda url: 404)
    report = json.loads((tmp_path / 'check-report.json').read_text())
    assert (tmp_path / 'raw' / 'prompt.md').read_text().startswith('# Schools Document Hunt')
    assert report['attempts'][0]['usage'] == {'input_tokens': 100, 'output_tokens': 50}
    assert any('HTTP 404' in w for w in report['warnings'])


def test_unusable_repair_falls_back_to_the_first_answers_valid_leads():
    broken = sample_data()
    broken['leads'][0]['get_records']['verified_at_url'] = ''
    unusable = {'subject': 'x', 'intro': '', 'no_lead_reason': '', 'leads': 'not a list'}
    issue, report = research(config(), D, [], agent=FakeAgent(broken, unusable), fetch=no_fetch)
    assert len(issue.leads) == 2 and 'first answer' in report['attempts'][1]['fallback']


def test_a_repair_that_breaks_valid_leads_falls_back_to_the_first_answer():
    broken = sample_data()
    broken['leads'][0]['get_records']['verified_at_url'] = ''
    worse = sample_data()
    for lead in worse['leads']:
        lead['source']['url'] = 'not a link'
    issue, report = research(config(), D, [], agent=FakeAgent(broken, worse), fetch=no_fetch)
    assert len(issue.leads) == 2 and 'first answer' in report['attempts'][1]['fallback']


def test_preview_refuses_a_folder_with_foreign_files_before_research(tmp_path):
    (tmp_path / 'source-receipts.json').write_text('[]')
    with pytest.raises(ValueError, match='did not write'):
        preview(config(), tmp_path, local_date=D, synthetic=False, agent=NoCalls(), fetch=no_fetch)


def test_rerunning_a_preview_in_the_same_folder_replaces_old_diagnostics(tmp_path):
    from dochunt.artifacts import load_preview
    preview(config(), tmp_path, local_date=D, synthetic=False, agent=FakeAgent(), fetch=no_fetch)
    (tmp_path / 'raw' / 'agent-output-2.json').write_text('stale')
    preview(config(), tmp_path, local_date=D, synthetic=False, agent=FakeAgent(), fetch=no_fetch)
    assert not (tmp_path / 'raw' / 'agent-output-2.json').exists()
    load_preview(tmp_path, config(), require_paid=True)


def test_carriage_returns_in_intro_still_produce_a_deliverable_preview(tmp_path):
    from dochunt.artifacts import load_preview
    data = sample_data()
    data['intro'] = 'Line one\r\nLine two\x07'
    preview(config(), tmp_path, local_date=D, synthetic=False, agent=FakeAgent(data), fetch=no_fetch)
    issue, _ = load_preview(tmp_path, config(), require_paid=True)
    assert issue.intro == 'Line one\nLine two'


def test_a_run_without_web_access_fails_visibly_and_writes_no_preview(tmp_path):
    blind = sample_data('no-leads')
    blind['checked_sources'] = []
    blind['no_lead_reason'] = 'The browsing tool was unavailable.'
    agent = FakeAgent(blind, blind)
    with pytest.raises(IssueFormatError, match='web access'):
        preview(config(), tmp_path, local_date=D, synthetic=False, agent=agent, fetch=no_fetch)
    assert len(agent.prompts) == 2 and not (tmp_path / 'issue.json').exists()


def test_report_records_the_sources_the_agent_checked():
    issue, report = research(config(), D, [], agent=FakeAgent(), fetch=no_fetch)
    assert report['checked_sources'] == sample_data()['checked_sources']
