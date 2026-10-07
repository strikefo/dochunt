from pathlib import Path
import pytest
from dochunt.config import load_config

def config(tmp_path, extra=''):
    p = tmp_path / 'newsroom.toml'
    p.write_text('''[identity]
id="example"
newsletter_name="Document Hunt"
sender_name="Newsroom"
[editorial]
coverage_brief="Public education"
[ai]
provider="openai"
model="gpt-5"
[email]
provider="resend"
sender="editor@example.org"
recipients=["reporter@example.org"]
[schedule]
timezone="America/Chicago"
''' + extra)
    return p

def test_disabled_defaults_and_exact_limits(tmp_path):
    c=load_config(config(tmp_path))
    assert c.schedule.enabled is False
    assert c.schedule.weekdays == [0,1,2,3,4]
    assert c.schedule.preferred_time == '08:00'
    assert vars(c.limits)==dict(workflow_timeout_minutes=30,application_deadline_minutes=25,max_candidates=20,max_final_leads=8,research_requests=12,review_requests=7,searches=20,fetches=30,input_tokens=120000,output_tokens=24000,transient_retries=2)

def test_plain_language_without_sources_is_valid(tmp_path):
    assert load_config(config(tmp_path)).sources == []

def test_multiline_unicode_configuration_round_trip(tmp_path):
    p=config(tmp_path)
    p.write_text(p.read_text().replace('"Public education"', '\"\"\"Éducation\nSchools and students.\"\"\"'))
    assert load_config(p).editorial.coverage_brief == 'Éducation\nSchools and students.'

def test_required_timezone_and_supported_provider(tmp_path):
    p=config(tmp_path)
    original=p.read_text()
    for text in [original.replace('timezone="America/Chicago"',''), original.replace('provider="openai"','provider="unknown"'), original.replace('America/Chicago','Invalid/Zone')]:
        p.write_text(text)
        with pytest.raises(ValueError): load_config(p)

def test_secret_values_are_not_serialized(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','private-test-key')
    c=load_config(config(tmp_path))
    assert 'private-test-key' not in repr(c)
    assert c.secret_names['ai'] == 'OPENAI_API_KEY'
    p=config(tmp_path)
    p.write_text(p.read_text().replace('[ai]', '[ai]\napi_key="private-test-key"'))
    with pytest.raises(ValueError): load_config(p)

@pytest.mark.parametrize('replacement', [
    ('[schedule]', '[schedule]\nenabled="false"'),
    ('[schedule]', '[schedule]\nweekdays=[true]'),
    ('[schedule]', '[schedule]\npreferred_time="25:00"'),
    ('sender="editor@example.org"', 'sender="invalid"'),
    ('[ai]', '[ai]\nunknown="value"'),
    ('[editorial]', '[editorial]\nmax_final_leads=9'),
])
def test_invalid_config_values_fail_early(tmp_path,replacement):
    p=config(tmp_path)
    p.write_text(p.read_text().replace(*replacement))
    with pytest.raises(ValueError): load_config(p)


def test_sender_name_header_controls_rejected():
    from dochunt.config import config_from_data
    import tomllib
    from pathlib import Path
    for value in ('News\r\nBcc: bad@example.invalid','News\x00','News\x7f'):
        data=tomllib.loads(Path('examples/education.toml').read_text()); data['identity']['sender_name']=value
        with pytest.raises(ValueError,match='sender name'): config_from_data(data)


def test_reasoning_and_reviewer_settings_roundtrip_without_schedule_activation(tmp_path):
    from dataclasses import asdict
    from dochunt.github_setup import serialize_config
    from dochunt.setup_server import form_config
    c=load_config(Path('examples/education.toml'))
    data=asdict(c); data['ai'].update(reasoning_effort='high',reviewer_model='gpt-6-astra')
    updated=form_config(data,c)
    path=tmp_path/'newsroom.toml'; path.write_text(serialize_config(updated))
    restored=load_config(path)
    assert restored.ai.reasoning_effort=='high' and restored.ai.reviewer_model=='gpt-6-astra'
    assert restored.schedule.enabled is False


def test_unsupported_reasoning_effort_is_rejected_before_paid_call(tmp_path):
    from dataclasses import asdict
    from dochunt.config import config_from_data
    c=load_config(Path('examples/education.toml')); data=asdict(c); data['ai']['reasoning_effort']='minimal'
    with pytest.raises(ValueError,match='reasoning effort'): config_from_data(data)
