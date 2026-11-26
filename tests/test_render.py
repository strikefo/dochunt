import pytest
from support import config, sample_data, sample_issue
from dochunt.models import parse_issue_data
from dochunt.render import AI_NOTICE, render_issue
from datetime import date


def issue_from(data):
    return parse_issue_data(data, issue_key='x:2026-10-02', local_date=date(2026, 10, 2))


def test_layout_matches_the_working_briefing():
    c = config()
    r = render_issue(sample_issue(c), c)
    assert r.subject == sample_data()['subject']
    for part in ('Schools Document Hunt', 'October 2, 2026', AI_NOTICE, '1. Did Example County pay',
                 'Reporting opportunity:', 'Source:', 'Get the records:', 'File this request:',
                 'Call or email first:', 'Public download:', 'Caveat:'):
        assert (part in r.text or part.upper() in r.text) and part.replace("'", '&#x27;') in r.html, part
    assert r.text.index(AI_NOTICE) < r.text.index('1. Did Example County')


def test_contacts_are_real_links_in_html_and_spelled_out_in_text():
    c = config()
    r = render_issue(sample_issue(c), c)
    assert 'href="https://publicworks.example/records-request"' in r.html
    assert 'href="mailto:facilities@schools.example"' in r.html and 'href="tel:5550102040"' in r.html
    assert 'Email: facilities@schools.example | Phone: (555) 010-2040' in r.text
    assert 'Route checked on October 2, 2026: https://publicworks.example/records' in r.text
    assert 'https://transit.example/plans/draft-2027-2031.pdf' in r.text


def test_request_paragraphs_and_dollar_amounts_survive():
    c = config()
    r = render_issue(sample_issue(c), c)
    assert 'fees will exceed $25.' in r.text and 'fees will exceed $25.' in r.html
    assert '$1.2 million' in r.subject and r.html.count('<p style="margin:0 0 12px;font-size:16px;line-height:1.6;">') >= 5
    assert '\n\nPlease provide records in their existing electronic format' in r.text


def test_internal_scores_and_notes_are_never_shown():
    c = config()
    r = render_issue(sample_issue(c), c)
    for hidden in ('Narrowed to the 30 sampled contracts', 'newsworthiness', 'obtainability', 'reviewer'):
        assert hidden.lower() not in r.html.lower() and hidden.lower() not in r.text.lower()


def test_content_is_escaped():
    data = sample_data()
    data['leads'][0]['headline'] = '<script>alert(1)</script> & "quotes"'
    data['leads'][0]['get_records']['text'] = 'Request <b>bold</b>'
    c = config()
    r = render_issue(issue_from(data), c)
    assert '<script>' not in r.html and '&lt;script&gt;' in r.html and '&lt;b&gt;bold' in r.html
    assert '<script>alert(1)</script>' in r.text


def test_no_lead_email_is_short_and_factual():
    c = config()
    r = render_issue(issue_from(sample_data('no-leads')), c)
    assert r.subject == 'No actionable records leads qualified today'
    assert 'none pointed to records' in r.text and 'Get the records' not in r.text and AI_NOTICE in r.text


def test_accent_branding_changes_deterministic_payload():
    c = config()
    issue = sample_issue(c)
    before = render_issue(issue, c)
    c.identity.accent_color = '#abcdef'
    after = render_issue(issue, c)
    assert 'background:#abcdef;color:#17202a' in after.html
    assert after.fingerprint != before.fingerprint and after == render_issue(issue, c)


def test_email_uses_inline_tables_and_wraps_long_text_on_mobile():
    c = config()
    r = render_issue(sample_issue(c), c)
    assert 'role="presentation"' in r.html and 'max-width:680px' in r.html
    assert 'overflow-wrap:anywhere' in r.html and '@media only screen and (max-width:600px)' in r.html
    assert '<img' not in r.html and '<script' not in r.html and 'https://fonts' not in r.html


def test_render_refuses_an_issue_that_fails_checks():
    c = config()
    issue = sample_issue(c)
    issue.leads[0].source_url = 'javascript:alert(1)'
    with pytest.raises(ValueError, match='failed checks'):
        render_issue(issue, c)


@pytest.mark.parametrize('phone,href', [('(202) 555-0100 ext. 123', 'tel:2025550100;ext=123'),
                                        ('+1 202 555 0100 x45', 'tel:+12025550100;ext=45'),
                                        ('(555) 010-2040', 'tel:5550102040')])
def test_phone_links_keep_extensions_separate(phone, href):
    from dochunt.render import _phone_href
    assert _phone_href(phone) == href
