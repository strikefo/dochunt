from datetime import date
import re
import pytest
from support import config
from dochunt.prompt import fill, hunt_prompt, repair_prompt, tracker_summary

D = date(2026, 11, 20)


def test_prompt_carries_the_beat_and_leaves_no_placeholders():
    c = config('examples/military.toml')
    text = hunt_prompt(c, D, [])
    assert '{{' not in text and '}}' not in text
    assert 'Military Document Hunt' in text and 'November 20, 2026' in text
    assert 'Military, defense and veterans' in text and 'Regulations.gov dockets' in text
    assert 'the federal Freedom of Information Act (FOIA)' in text and 'at most 3 leads' in text
    assert 'last 3 days' in text and 'No leads have been sent yet.' in text
    assert 'do not send email' in text.lower() or 'You do not send email' in text


def test_state_records_law_replaces_foia():
    text = hunt_prompt(config('examples/education.toml'), D, [])
    assert 'Wisconsin Public Records Law' in text and 'Geography: Wisconsin' in text


def test_placeholder_like_text_in_config_is_not_expanded():
    c = config()
    c.beat.brief = 'Watch {{records_law}} and {{unknown}} literally.'
    assert 'Watch {{records_law}} and {{unknown}} literally.' in hunt_prompt(c, D, [])


def test_unknown_template_placeholder_is_an_error():
    with pytest.raises(KeyError):
        fill('{{missing}}', {})


def test_tracker_lists_recent_sent_leads_newest_first_and_bounded():
    rows = [{'last_issue_included': f'2026-11-{d:02d}', 'headline': f'H{d}', 'agency': 'A', 'source_url': f'https://x.example/{d}'} for d in range(1, 20)]
    rows.append({'last_issue_included': '2026-01-01', 'headline': 'Old', 'agency': 'A', 'source_url': 'https://x.example/old'})
    text = tracker_summary(rows, D)
    assert text.index('H19') < text.index('H1"') and 'Old' not in text


def test_extra_instructions_get_their_own_section():
    c = config()
    c.beat.extra_instructions = 'Always check the district\'s board packets.'
    assert "## Newsroom instructions\n\nAlways check the district's board packets." in hunt_prompt(c, D, [])
    c.beat.extra_instructions = ''
    assert '## Newsroom instructions' not in hunt_prompt(c, D, [])


def test_repair_prompt_lists_problems_and_embeds_previous_output():
    original = hunt_prompt(config(), D, [])
    text = repair_prompt(config(), D, original, {'subject': 'S $25'}, ['lead 1: problem one', 'subject is empty'])
    assert '- lead 1: problem one\n- subject is empty' in text and '"subject": "S $25"' in text
    assert text.endswith(original + '\n') or original in text
    assert not re.search(r'\{\{[a-z_]+\}\}', text.split('---')[0])
