"""Deterministic, table-based email rendering of finalized issue records."""
from dataclasses import dataclass
from html import escape
import re
from .editorial import validate_issue, _digest


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


def render_issue(issue, config):
    validate_issue(issue, config)
    esc = lambda x: escape(str(x), quote=True)
    title = config.identity.newsletter_name
    day = issue.local_date.strftime('%B ') + str(issue.local_date.day) + issue.local_date.strftime(', %Y')
    accent = config.identity.accent_color
    foreground = _contrast(accent)
    date_color = '#d9e5f0' if foreground == '#ffffff' else '#334155'
    html = [f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)}</title><style>td,p,a,h1{{overflow-wrap:anywhere;word-break:break-word;}}@media only screen and (max-width:600px){{.email-side{{padding-left:20px!important;padding-right:20px!important}}.records-panel{{padding:18px!important}}}}</style></head>
<body style="margin:0;background:#eef1f4;color:#17202a;font-family:Arial,Helvetica,sans-serif;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#eef1f4;"><tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="max-width:680px;table-layout:fixed;background:#ffffff;border:1px solid #d8dee5;">
<tr><td class="email-side" style="padding:34px 38px 20px;text-align:center;background:{esc(accent)};color:{foreground};"><div style="font-size:30px;line-height:1.15;font-weight:700;letter-spacing:.2px;">{esc(title)}</div><div style="margin-top:9px;font-size:16px;line-height:1.4;color:{date_color};">{esc(day)}</div></td></tr>''']
    text = [title, day, '']

    def paragraph(label, value, *, font=18, margin=16, color=None, link=None, link_label="Official records procedure"):
        # Escape every prose line before preserving line breaks; blank lines
        # still create separate copy-ready paragraphs.
        parts = [part.strip() for part in value.split('\n\n') if part.strip()]
        for index, part in enumerate(parts):
            prefix = f'<strong>{esc(label)}:</strong> ' if label and index == 0 else ''
            route = f' <a href="{esc(link)}" style="color:#145da0;">{esc(link_label)}</a>.' if link and index == len(parts) - 1 else ''
            spacing = 14 if index < len(parts) - 1 else margin
            style = f'margin:0 0 {spacing}px;font-size:{font}px;line-height:{1.65 if font == 18 else 1.62};'
            if color: style += f'color:{color};'
            lines = '<br>'.join(esc(line) for line in part.split('\n'))
            html.append(f'<p style="{style}">{prefix}{lines}{route}</p>')
        text.append((f'{label}: ' if label else '') + '\n\n'.join(parts) + (f' {link_label}: {link}' if link else ''))
        text.append('')

    html.append('<tr><td class="email-side" style="padding:24px 38px 0;">')
    if issue.leads:
        disclaimer = 'This briefing is AI-generated and may contain errors. Please double-check facts, contacts and request details before acting.'
        html.append(f'<div style="padding:16px 18px;background:#fff5cf;border-left:4px solid #c89211;font-size:17px;line-height:1.55;"><strong>{esc(disclaimer)}</strong></div>')
        text.extend([disclaimer, ''])
    else:
        paragraph('', 'No qualifying document leads were found in this completed research run.')
    html.append('</td></tr>')
    verify_started = False
    for number, lead in enumerate(issue.leads, 1):
        if lead.records_plan.get('tier') == 'verify_route' and not verify_started:
            verify_started = True
            html.append('<tr><td class="email-side" style="padding:34px 38px 0;"><div style="border-top:3px solid #c89211;padding-top:18px;'
                        'font-size:20px;font-weight:700;color:#173b63;">Check the request route first</div>'
                        '<p style="margin:8px 0 0;font-size:16px;line-height:1.55;color:#4b5563;">The facts below are quoted from new documents and '
                        'passed review, but DocHunt could not confirm where to send the request. Confirm the route before filing.</p></td></tr>')
            text.extend(['CHECK THE REQUEST ROUTE FIRST',
                         'The facts below are quoted from new documents and passed review, but DocHunt could not confirm where to send the request.', ''])
        padding = '30px 38px 4px' if number == 1 else '34px 38px 4px'
        html.append(f'<tr><td class="email-side" style="padding:{padding};">')
        if number > 1: html.append('<div style="border-top:1px solid #d8dee5;padding-top:30px;">')
        html.append(f'<div style="font-size:13px;line-height:1.3;font-weight:700;letter-spacing:1px;color:#64748b;text-transform:uppercase;">Lead {number}</div>')
        headline = f'{number}. {lead.provisional_headline}'
        html.append(f'<h1 style="margin:7px 0 18px;font-size:27px;line-height:1.25;color:#173b63;">{esc(headline)}</h1>')
        text.extend([headline, ''])
        hook = lead.factual_hook.strip()
        new = lead.records_plan.get('new_document') or {}
        if new.get('published'):
            from datetime import date as _date
            day = _date.fromisoformat(new['published'])
            shown = day.strftime('%B %Y') if new.get('approximate') else day.strftime('%B ') + str(day.day) + day.strftime(', %Y')
            title = re.split(r'\s+[|\u2014]\s+', new['title'])[0].strip()  # drop " | U.S. GAO" style suffixes
            paragraph('New document', f"{title}, published {shown}", link=new['url'], link_label='Open')
        paragraph('Established', hook)
        paragraph('Still to prove', lead.story_potential.strip())
        source = next(e for e in issue.evidence if e.id in lead.evidence_ids and e.url == lead.source_url)
        if (lead.records_plan.get('new_document') or {}).get('url') != source.url:
            html.append(f'<p style="margin:0 0 20px;font-size:17px;line-height:1.6;"><strong>Source:</strong> <a href="{esc(source.url)}" style="color:#145da0;">{esc(source.title)}</a></p>')
            text.extend([f'Source: {source.title} — {source.url}', ''])
        html.append('<div class="records-panel" style="padding:20px 22px;background:#f2f6fa;border:1px solid #d7e1eb;font-size:17px;line-height:1.62;">')
        records = [r for r in lead.records_plan['records'] if r.get('essential')]
        routes = []  # each confirmed route shown once, after the records it serves
        for index, record in enumerate(records):
            route = 'Public download' if record['route'] == 'public_download' else 'Request'
            if not record.get('evidence_id'):
                route = 'Route to confirm'
            # One line per record: what it is, and how to get it.
            line = record['description'].strip().rstrip('.') + f' \u2014 {route}.'
            paragraph('Get the records' if index == 0 else '', line, font=17, margin=8)
            # Only confirmed routes are shown as where to go; never a link for an unconfirmed one.
            if record.get('evidence_id') and record.get('location', '').strip() and (route, record['location']) not in routes:
                routes.append((route, record['location'].strip()))
        for index, (route, location) in enumerate(routes):
            last = index == len(routes) - 1 and not lead.records_request_text.strip()
            if re.match(r'https?://', location):
                paragraph('', 'Submit the request online' if route == 'Request' else 'Download', font=17,
                          margin=0 if last else 8, link=location, link_label='Open')
            else:  # an email or mailing address, shown as text
                paragraph('', ('Send the request to: ' if route == 'Request' else 'Get it from: ') + location, font=17,
                          margin=0 if last else 8)
        if lead.records_request_text.strip():
            paragraph('', lead.records_request_text, font=17, margin=0)
        html.append('</div>')
        notes = lead.records_plan.get('route_notes') or []
        if notes:
            html.append('<div style="margin:14px 0 0;">')
            paragraph('To confirm', ' '.join(notes), font=16, margin=0, color='#7a4b00')
            html.append('</div>')
        if lead.caveat.strip():
            html.append('<div style="margin:14px 0 0;">')
            paragraph('Caveat', lead.caveat, font=16, margin=0, color='#4b5563')
            html.append('</div>')
        if number > 1: html.append('</div>')
        html.append('</td></tr>')
    footer = 'Editorial research aid prepared from source records. Verify details before filing.'
    html.append(f'<tr><td class="email-side" style="padding:30px 38px 34px;"><div style="border-top:1px solid #d8dee5;padding-top:20px;font-size:16px;line-height:1.5;color:#64748b;">{esc(footer)}</div></td></tr>')
    text.extend([footer, ''])
    html.append('</table></td></tr></table></body></html>')
    html = '\n'.join(html)
    text = '\n'.join(text).rstrip() + '\n'
    return RenderedIssue(issue.subject, html, text, _digest({'subject':issue.subject, 'html':html, 'text':text}))
