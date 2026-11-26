from dataclasses import asdict
from pathlib import Path
import tomllib
import pytest
from dochunt.config import ConfigError, config_from_data, load_config

MINIMAL = '''[identity]
id="example"
newsletter_name="Document Hunt"
sender_name="Newsroom"
[beat]
name="Public education"
brief="Public education in Wisconsin"
[agent]
runtime="codex"
[email]
provider="resend"
sender="editor@example.org"
recipients=["reporter@example.org"]
[schedule]
timezone="America/Chicago"
'''


def config(tmp_path, text=MINIMAL):
    p = tmp_path / 'newsroom.toml'
    p.write_text(text)
    return p


def test_minimal_config_has_safe_defaults(tmp_path):
    c = load_config(config(tmp_path))
    assert c.schedule.enabled is False and c.schedule.weekdays == [0, 1, 2, 3, 4] and c.schedule.preferred_time == '08:00'
    assert c.beat.max_leads == 3 and c.beat.freshness_days == 3 and 'Freedom of Information Act' in c.beat.records_law
    assert c.agent.timeout_minutes == 18 and c.agent.max_budget_usd == 5.0
    assert c.model == 'gpt-6.1-sol' and c.secret_names == {'ai': 'OPENAI_API_KEY', 'email': 'RESEND_API_KEY'}


def test_claude_runtime_needs_the_anthropic_key_and_integer_budget_is_accepted(tmp_path):
    c = load_config(config(tmp_path, MINIMAL.replace('runtime="codex"', 'runtime="claude"\nmax_budget_usd=3')))
    assert c.secret_names['ai'] == 'ANTHROPIC_API_KEY' and c.agent.max_budget_usd == 3.0 and c.model == 'claude-opus-5-5'


def test_every_example_is_valid_fictional_and_unscheduled():
    for path in Path('examples').glob('*.toml'):
        c = load_config(path)
        assert c.schedule.enabled is False, path
        assert all(r.endswith('@example.org') for r in c.email.recipients), path


def test_this_installations_config_is_valid():
    load_config(Path('newsroom.toml'))  # a newsroom's own settings: real addresses and schedule are its choice


def test_multiline_unicode_brief_round_trip(tmp_path):
    p = config(tmp_path, MINIMAL.replace('brief="Public education in Wisconsin"', 'brief="""Éducation\nSchools and students."""'))
    assert load_config(p).beat.brief == 'Éducation\nSchools and students.'


def test_secret_values_are_never_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'private-test-key')
    assert 'private-test-key' not in repr(load_config(config(tmp_path)))
    with pytest.raises(ConfigError):
        load_config(config(tmp_path, MINIMAL.replace('[agent]', '[agent]\napi_key="private-test-key"')))


@pytest.mark.parametrize('old,new', [
    ('timezone="America/Chicago"', ''),
    ('America/Chicago', 'Invalid/Zone'),
    ('runtime="codex"', 'runtime="gpt"'),
    ('[schedule]', '[schedule]\nenabled="false"'),
    ('[schedule]', '[schedule]\nweekdays=[7]'),
    ('[schedule]', '[schedule]\npreferred_time="25:00"'),
    ('[schedule]', '[schedule]\nwindow_hours=0'),
    ('[schedule]', '[schedule]\nwindow_hours=24'),
    ('sender="editor@example.org"', 'sender="invalid"'),
    ('recipients=["reporter@example.org"]', 'recipients=[]'),
    ('recipients=["reporter@example.org"]', 'recipients=["a@example.org","a@example.org"]'),
    ('[beat]', '[beat]\nmax_leads=6'),
    ('[beat]', '[beat]\nfreshness_days=0'),
    ('[beat]', '[beat]\npriorities=["ok", ""]'),
    ('name="Public education"', 'name="Two\\nlines"'),
    ('[agent]', '[agent]\ntimeout_minutes=25'),
    ('[agent]', '[agent]\nmax_budget_usd=100'),
    ('[agent]', '[agent]\nreasoning_effort="minimal"'),
    ('[agent]', '[agent]\nmodel="bad model; rm"'),
    ('[agent]', '[agent]\nunknown="value"'),
    ('runtime="codex"', 'runtime="claude"\nmodel="gpt-6.1-sol"'),
    ('runtime="codex"', 'runtime="codex"\nmodel="claude-opus-5-5"'),
    ('[identity]', '[editorial]\ncoverage_brief="old format"\n[identity]'),
])
def test_invalid_values_fail_early_with_a_readable_message(tmp_path, old, new):
    with pytest.raises(ConfigError) as error:
        load_config(config(tmp_path, MINIMAL.replace(old, new)))
    assert str(error.value)


def test_sender_name_header_controls_rejected():
    for value in ('News\r\nBcc: bad@example.invalid', 'News\x00', 'News\x7f'):
        data = tomllib.loads(Path('examples/education.toml').read_text())
        data['identity']['sender_name'] = value
        with pytest.raises(ConfigError, match='sender_name'):
            config_from_data(data)


def test_wizard_serialization_round_trips_beat_and_agent(tmp_path):
    from dochunt.github_setup import serialize_config
    from dochunt.setup_server import form_config
    c = load_config(Path('examples/education.toml'))
    data = asdict(c)
    data['agent'].update(runtime='codex', model='', reasoning_effort='medium', max_budget_usd=2.5)
    data['beat'].update(priorities=['Safety', 'Money "quoted"'], extra_instructions='Line one\nLine two')
    path = tmp_path / 'newsroom.toml'
    path.write_text(serialize_config(form_config(data, c)))
    restored = load_config(path)
    assert asdict(restored)['agent'] == data['agent'] and restored.beat.priorities == ['Safety', 'Money "quoted"']
    assert restored.beat.extra_instructions == 'Line one\nLine two' and restored.schedule.enabled is False
