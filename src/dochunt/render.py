"""Deterministic, email-safe rendering of a checked issue: branded HTML plus matching plain text."""
from dataclasses import dataclass
import hashlib
from html import escape
import re
from .models import Issue, issue_problems
from .prompt import long_date

AI_NOTICE = ('This briefing is AI-generated and may contain errors. Please double-check facts, '
             'contacts and request details before acting.')
FOOTER = 'Editorial research aid. Verify facts, scope and procedure before reporting or filing. DocHunt never files requests.'
ACTION_LABELS = {'foia': 'File this request', 'inquiry': 'Call or email first', 'download': 'Public download'}


@dataclass(frozen=True)
class RenderedIssue:
    subject: str
    html: str
    text: str
    fingerprint: str


def _contrast(color):
    """Choose legible masthead text for the configured six-digit accent."""
    rgb = [int(color[i:i+2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in rgb]
    luminance = sum(c * weight for c, weight in zip(linear, (.2126, .7152, .0722)))
    return '#17202a' if luminance > .179 else '#ffffff'


def _paragraphs(text):
    return [' '.join(p.split()) for p in re.split(r'\n\s*\n', text.strip()) if p.strip()]


def _esc(value):
    return escape(str(value), quote=True)


def _link(url, label, color):
    return f'<a href="{_esc(url)}" style="color:{color};text-decoration:underline;word-break:break-word;">{_esc(label)}</a>'


def _phone_href(phone):
    """RFC 3966 tel: URI; an extension stays separate instead of being dialed as part of the number."""
    match = re.search(r'\s*(?:ext\.?|extension|x|#)\s*(\d+)\s*$', phone.strip(), re.I)
    number = phone.strip()[:match.start()] if match else phone
    digits = re.sub(r'[^\d+]', '', number)
    return 'tel:' + digits + (f';ext={match.group(1)}' if match else '')


def render_issue(issue: Issue, config) -> RenderedIssue:
    from .subscriptions import manage_link
    manage_url = manage_link(config.email)
    problems = issue_problems(issue)
    if problems:
        raise ValueError('Issue failed checks: ' + '; '.join(problems))
    title = config.identity.newsletter_name
    day = long_date(issue.local_date)
    accent = config.identity.accent_color
    fg = _contrast(accent)
    sub = '#d9e5f0' if fg == '#ffffff' else '#334155'
    link_color = '#155a92'
    body = 'font-size:17px;line-height:1.6;margin:0 0 14px;'

    html = [f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{_esc(title)} — {_esc(day)}</title><style>td,p,a,h1,h2{{overflow-wrap:anywhere;word-break:break-word;}}@media only screen and (max-width:600px){{.side{{padding-left:20px!important;padding-right:20px!important}}.panel{{padding:16px!important}}}}</style></head>
<body style="margin:0;background:#eef1f4;color:#17202a;font-family:Arial,Helvetica,sans-serif;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{_esc(issue.subject)}</div>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#eef1f4;"><tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="max-width:680px;table-layout:fixed;background:#ffffff;border:1px solid #d8dee5;">
<tr><td class="side" style="padding:32px 38px 22px;text-align:center;background:{_esc(accent)};color:{fg};"><h1 style="margin:0;font-size:30px;line-height:1.15;">{_esc(title)}</h1><div style="margin-top:8px;font-size:16px;color:{sub};">{_esc(day)}</div></td></tr>
<tr><td class="side" style="padding:24px 38px 8px;">
<p style="margin:0 0 18px;padding:14px 16px;background:#fff4d8;border-left:4px solid #d39b2a;font-size:16px;line-height:1.5;"><strong>{_esc(AI_NOTICE)}</strong></p>''']
    text = [title.upper(), day, '', AI_NOTICE, '']

    if issue.intro:
        html.append(f'<p style="{body}font-size:18px;">{_esc(issue.intro)}</p>')
        text += [issue.intro, '']
    if not issue.leads:
        html.append(f'<p style="{body}font-size:18px;"><strong>No actionable records leads qualified today.</strong></p>')
        html.append(f'<p style="{body}">{_esc(issue.no_lead_reason)}</p>')
        text += ['No actionable records leads qualified today.', '', issue.no_lead_reason, '']
    html.append('</td></tr>')

    for n, lead in enumerate(issue.leads, 1):
        r, c = lead.get_records, lead.get_records.contact
        border = 'border-top:1px solid #d8dee5;' if n > 1 else ''
        html.append(f'<tr><td class="side" style="padding:26px 38px 10px;{border}">')
        html.append(f'<h2 style="margin:0 0 14px;font-size:24px;line-height:1.3;color:#102f4d;">{n}. {_esc(lead.headline)}</h2>')
        html.append(f'<p style="{body}"><strong>Reporting opportunity:</strong> {_esc(lead.reporting_opportunity)}</p>')
        html.append(f'<p style="{body}font-size:16px;"><strong>Source:</strong> {_link(lead.source_url, lead.source_title, link_color)}</p>')
        text += [f'{n}. {lead.headline}', '', f'Reporting opportunity: {lead.reporting_opportunity}', '',
                 f'Source: {lead.source_title}', lead.source_url, '']

        office = r.agency + (f', {r.office}' if r.office else '')
        html.append(f'<div class="panel" style="background:#f3f6f9;border-left:4px solid {_esc(accent)};padding:18px 20px;margin:4px 0 16px;">')
        html.append(f'<p style="margin:0 0 10px;font-size:17px;line-height:1.5;"><strong>Get the records:</strong> {_esc(office)}</p>')
        text += ['Get the records:', office]
        contact_bits, contact_text = [], []
        if c.url:
            contact_bits.append(_link(c.url, c.label or c.url, link_color))
            contact_text.append(f'{c.label + ": " if c.label else ""}{c.url}')
        if c.email:
            contact_bits.append(_link('mailto:' + c.email, c.email, link_color))
            contact_text.append(f'Email: {c.email}')
        if c.phone:
            contact_bits.append(_link(_phone_href(c.phone), c.phone, link_color))
            contact_text.append(f'Phone: {c.phone}')
        lead_label = {'foia': 'Submit', 'inquiry': 'Contact', 'download': 'Download'}[r.action]
        html.append(f'<p style="margin:0 0 10px;font-size:16px;line-height:1.5;"><strong>{lead_label}:</strong> {" · ".join(contact_bits)}</p>')
        text += [f'{lead_label}: ' + ' | '.join(contact_text)]
        if r.verified_at_url:
            html.append(f'<p style="margin:0 0 12px;font-size:14px;line-height:1.5;color:#526579;">Route checked on {_link(r.verified_at_url, "this official page", link_color)} on {_esc(day)}.</p>')
            text += [f'Route checked on {day}: {r.verified_at_url}']
        html.append(f'<p style="margin:0 0 10px;font-size:16px;"><strong>{ACTION_LABELS[r.action]}:</strong></p>')
        text += ['', f'{ACTION_LABELS[r.action]}:', '']
        for p in _paragraphs(r.text):
            html.append(f'<p style="margin:0 0 12px;font-size:16px;line-height:1.6;">{_esc(p)}</p>')
            text += [p, '']
        html.append('</div>')
        if lead.caveat:
            html.append(f'<p style="margin:0 0 14px;font-size:15px;line-height:1.5;color:#4d5962;"><strong>Caveat:</strong> {_esc(lead.caveat)}</p>')
            text += [f'Caveat: {lead.caveat}', '']
        html.append('</td></tr>')

    manage_html = (f'<br>{_link(manage_url, "Manage subscription", link_color)} — opt in or opt out.') if manage_url else ''
    html.append(f'<tr><td class="side" style="padding:18px 38px 26px;border-top:1px solid #d8dee5;color:#617081;font-size:13px;line-height:1.5;">{_esc(FOOTER)}{manage_html}</td></tr>')
    html.append('</table></td></tr></table></body></html>\n')
    text += ['—', FOOTER] + ([f'Manage subscription (opt in or opt out): {manage_url}'] if manage_url else []) + ['']
    html_s, text_s = '\n'.join(html), '\n'.join(text)
    fingerprint = hashlib.sha256('\0'.join((issue.subject, html_s, text_s)).encode()).hexdigest()
    return RenderedIssue(issue.subject, html_s, text_s, fingerprint)
