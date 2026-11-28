"""One hunt: prompt, agent, checks, at most one repair, render, and preview artifacts."""
from datetime import date
from importlib.resources import files
import json
import os
from pathlib import Path
import time
from .agents import AgentError, run_agent
from .checks import assess, finalize, http_status, warnings_for
from .models import IssueFormatError, issue_to_data, parse_issue_data
from .prompt import hunt_prompt, repair_prompt
from .render import render_issue
from .tracker import sent_keys


def fixture(name='sample'):
    return json.loads(files('dochunt').joinpath('fixtures', f'{name}-issue.json').read_text(encoding='utf-8'))


def issue_key(config, local_date):
    return f'{config.identity.id}:{local_date.isoformat()}'


MIN_REPAIR_SECONDS = 240


def _remaining(deadline):
    """Seconds left before the workflow's application deadline (epoch seconds), or None."""
    if deadline is None:
        value = os.environ.get('DOCHUNT_DEADLINE', '')
        deadline = int(value) if value.isdigit() else None
    return None if deadline is None else deadline - time.time() - 60


def _time_left(deadline):
    remaining = _remaining(deadline)
    return float('inf') if remaining is None else remaining + 60


def research(config, local_date, tracker_rows, *, raw_dir=None, agent=run_agent, fetch=http_status, deadline=None):
    """Run the agent and return (issue, report). Raises AgentError or IssueFormatError when nothing sendable exists."""
    sent = sent_keys(tracker_rows)
    prompt = hunt_prompt(config, local_date, tracker_rows)
    report = {'runtime': config.agent.runtime, 'model': config.model, 'attempts': []}
    if raw_dir:
        Path(raw_dir).mkdir(parents=True, exist_ok=True)
        (Path(raw_dir) / 'prompt.md').write_text(prompt, encoding='utf-8')
    current_prompt, assessment, first = prompt, None, None
    for attempt in (1, 2):
        limit = config.agent.timeout_minutes * 60
        remaining = _remaining(deadline)
        if remaining is not None:
            if attempt == 2 and remaining < MIN_REPAIR_SECONDS:
                report['attempts'].append({'attempt': attempt, 'skipped': True,
                                           'error': 'Skipped repair: not enough time before the run deadline'})
                break
            limit = max(60, min(limit, int(remaining)))
        try:
            result = agent(config, current_prompt, timeout=limit)
        except AgentError as exc:
            report['attempts'].append({'attempt': attempt, 'error': str(exc)})
            if assessment is None:
                raise
            break  # A failed repair falls back to the first answer's valid leads.
        if raw_dir:
            (Path(raw_dir) / f'agent-output-{attempt}.json').write_text(result.raw, encoding='utf-8')
        assessment = assess(result.data, max_leads=config.beat.max_leads, sent=sent)
        if attempt == 1:
            first = assessment
        report['attempts'].append({'attempt': attempt, 'seconds': result.seconds, 'usage': result.usage,
                                   'problems': assessment.problems})
        if not assessment.problems:
            break
        if attempt == 1:
            current_prompt = repair_prompt(config, local_date, prompt, result.data if isinstance(result.data, dict) else result.raw,
                                           assessment.problems)
    key = issue_key(config, local_date)

    def final(a):
        return finalize(a, issue_key=key, local_date=local_date, max_leads=config.beat.max_leads)
    if first is None or first is assessment:
        issue, dropped = final(assessment)
    else:
        # A repair should only fix problems. If it is unusable, or leaves fewer valid leads than the
        # first answer already had, send the first answer's valid leads instead.
        repair_error = None
        try:
            repaired = final(assessment)
        except IssueFormatError as exc:
            repaired, repair_error = None, exc
        try:
            original = final(first)
        except IssueFormatError:
            original = None
        if repaired is None and original is None:
            raise repair_error
        if repaired is None or (original is not None and len(original[0].leads) > len(repaired[0].leads)):
            report['attempts'][-1]['fallback'] = 'Repair output unusable or worse; used the first answer'
            issue, dropped = original
            assessment = first
        else:
            issue, dropped = repaired
    report['checked_sources'] = assessment.checked_sources
    report['dropped'] = dropped
    report['warnings'] = warnings_for(issue, fetch, time_left=lambda: _time_left(deadline))
    return issue, report


def write_preview(config, issue, report, output_dir, *, synthetic):
    """Write the reviewed preview artifacts used by every later send."""
    from .artifacts import write_manifest
    rendered = render_issue(issue, config)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / 'preview.html').write_text(rendered.html, encoding='utf-8')
    (output_dir / 'preview.txt').write_text(rendered.text, encoding='utf-8')
    (output_dir / 'check-report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    data = {'issue_key': issue.issue_key, 'local_date': issue.local_date.isoformat(), 'fingerprint': rendered.fingerprint,
            'issue': issue_to_data(issue)}
    (output_dir / 'issue.json').write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    write_manifest(output_dir, config, synthetic)
    return rendered


def synthetic_issue(config, local_date: date, name='sample'):
    issue = parse_issue_data(fixture(name), issue_key=issue_key(config, local_date), local_date=local_date)
    return issue, {'runtime': 'synthetic', 'model': '', 'attempts': [], 'dropped': [], 'warnings': []}


def _check_output_dir(output_dir):
    """Refuse a folder holding other files before any billed work; clear our own raw/ diagnostics."""
    import shutil
    from .artifacts import FILES
    output_dir = Path(output_dir)
    if not output_dir.exists():
        return
    if not output_dir.is_dir():
        raise ValueError(f'Output path is not a folder: {output_dir}')
    other = sorted(p.name for p in output_dir.iterdir() if p.name not in FILES | {'raw'})
    if other:
        raise ValueError(f'Output folder has files DocHunt did not write ({", ".join(other[:5])}); use an empty folder')
    raw = output_dir / 'raw'
    if raw.is_symlink() or (raw.exists() and not raw.is_dir()):
        raise ValueError('Output folder has an unexpected raw entry; use an empty folder')
    if raw.exists():
        shutil.rmtree(raw)


def preview(config, output_dir, *, local_date, synthetic, tracker_rows=(), fixture_name='sample', agent=run_agent, fetch=http_status):
    """synthetic=True renders a free fictional issue; synthetic=False runs the research agent (billed). No default."""
    _check_output_dir(output_dir)
    if synthetic:
        issue, report = synthetic_issue(config, local_date, fixture_name)
    else:
        issue, report = research(config, local_date, list(tracker_rows), raw_dir=Path(output_dir) / 'raw', agent=agent, fetch=fetch)
    return write_preview(config, issue, report, output_dir, synthetic=synthetic)


__all__ = ['AgentError', 'IssueFormatError', 'preview', 'research', 'synthetic_issue', 'write_preview']
