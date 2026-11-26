"""Fill the agent prompt from newsroom configuration and the sent-lead tracker."""
from datetime import date, timedelta
from importlib.resources import files
import json
import re

TRACKER_DAYS = 120
TRACKER_ROWS = 150


def template(name):
    return files('dochunt').joinpath('prompts', name + '.md').read_text(encoding='utf-8')


def fill(text, values):
    def replace(match):
        key = match.group(1)
        if key not in values:
            raise KeyError(f'Prompt placeholder without a value: {key}')
        return values[key]
    # One pass, so placeholder-like text inside values (from config or the web) is never expanded.
    return re.sub(r'\{\{([a-z_]+)\}\}', replace, text)


def long_date(day: date) -> str:
    return f'{day.strftime("%B")} {day.day}, {day.year}'


def _bullets(items, empty):
    return '\n'.join(f'- {item}' for item in items) if items else f'- {empty}'


def tracker_summary(rows, local_date):
    cutoff = (local_date - timedelta(days=TRACKER_DAYS)).isoformat()
    recent = [r for r in rows if r.get('last_issue_included', '') >= cutoff]
    recent.sort(key=lambda r: r.get('last_issue_included', ''), reverse=True)
    recent = recent[:TRACKER_ROWS]
    if not recent:
        return 'No leads have been sent yet.'
    lines = [json.dumps({'sent': r.get('last_issue_included', ''), 'headline': r.get('headline', ''),
                         'agency': r.get('agency', ''), 'source_url': r.get('source_url', '')}, ensure_ascii=False)
             for r in recent]
    return 'Reference data, one JSON object per line:\n' + '\n'.join(lines)


def hunt_prompt(config, local_date, tracker_rows):
    beat = config.beat
    extra = beat.extra_instructions.strip()
    return fill(template('hunt'), {
        'newsletter_name': config.identity.newsletter_name,
        'date_long': long_date(local_date),
        'beat_name': beat.name,
        'geography': beat.geography or 'not limited',
        'brief': beat.brief.strip(),
        'priorities': _bullets(beat.priorities, 'Use the beat description above.'),
        'exclusions': beat.exclusions.strip() or 'Opinion, speculation and general news without a document trail.',
        'freshness_days': str(beat.freshness_days),
        'source_families': _bullets(beat.source_families, 'Official agency, oversight, legislative, court, contracting and data sources for this beat.'),
        'tracker': tracker_summary(tracker_rows, local_date),
        'max_leads': str(beat.max_leads),
        'records_law': beat.records_law,
        'extra_instructions': f'## Newsroom instructions\n\n{extra}' if extra else '',
    })


def repair_prompt(config, local_date, original, previous, problems):
    return fill(template('repair'), {
        'newsletter_name': config.identity.newsletter_name,
        'date_long': long_date(local_date),
        'problems': '\n'.join(f'- {p}' for p in problems),
        'previous': json.dumps(previous, ensure_ascii=False, indent=2) if not isinstance(previous, str) else previous,
        'original': original,
    })
