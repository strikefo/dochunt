"""Run frozen evaluation cases through the evidence engine.

Offline (default): replays recorded provider responses from --responses DIR/<case>/
{analysis,review}.json (OpenAI Responses wire format). No network is possible: the
transport is FixtureTransport and supplied-source cases never fetch.

Live (--live): sends the exact payloads to the configured provider. Requires an API key
in the environment and an explicit --max-usd ceiling. It costs money: run it only in
your own account, deliberately.

Writes, per case: engine-result.json, preview.html/.txt, and a usage/cost/latency row in
summary.json. Grading is separate: see eval/criteria.md.
"""
import argparse
import json
import os
import time
import tomllib
from datetime import date
from pathlib import Path

from dochunt.budget import Budget
from dochunt.config import load_config
from dochunt.engine import hunt_preview, source_from_receipt
from dochunt.models import Limits
from dochunt.providers import create_provider
from dochunt.providers.base import FixtureTransport, HTTPTransport

ROOT = Path(__file__).resolve().parents[1]


class RecordingTransport:
    """Writes every raw provider response to disk before the provider layer validates it,
    so truncated or malformed paid output is never lost. Requests (which carry only public
    source text and prompts) are not saved; headers, including the key, never are."""

    def __init__(self, inner, directory):
        self.inner, self.directory, self.count = inner, Path(directory), 0
        self.web, self.offline = inner.web, getattr(inner, 'offline', False)
        self.requests = getattr(inner, 'requests', None)

    def __call__(self, endpoint, headers, payload, timeout):
        wire = self.inner(endpoint, headers, payload, timeout)
        self.count += 1
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / f'wire-{self.count}.json').write_text(json.dumps(wire, ensure_ascii=False) + '\n')
        return wire


def case_config(case, base):
    config = load_config(base)
    config.editorial.coverage_brief = case['brief']
    config.editorial.geography = case['geography']
    config.hunt.mode = 'search'  # supplied sources only
    # Holds every supplied case whole (largest about 127K) and keeps analysis plus review
    # inside the per-case input allowance, which counts payload bytes before each call.
    config.hunt.max_source_chars = 130000
    config.hunt.playbook = str(ROOT / case['playbook']) if case.get('playbook') else ''
    return config


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--case', action='append', help='Case id prefix; repeatable. Default: all supplied cases.')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--config', type=Path, default=ROOT / 'newsroom.toml')
    parser.add_argument('--responses', type=Path, help='Recorded responses directory (offline)')
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--max-usd', type=float, default=0.0)
    parser.add_argument('--input-usd-per-million', type=float, default=2.5)
    parser.add_argument('--output-usd-per-million', type=float, default=10.0)
    args = parser.parse_args()

    cases = tomllib.loads((ROOT / 'eval/cases.toml').read_text())['case']
    chosen = [c for c in cases if (c['id'].startswith(tuple(args.case)) if args.case else c['track'] == 'supplied')]
    if args.live and args.max_usd <= 0:
        parser.error('--live requires a positive --max-usd ceiling')
    if not args.live and not args.responses:
        parser.error('offline runs need --responses')
    spent, rows = 0.0, []
    # No transient retries in evaluation: one attempt per stage, so cost is bounded and
    # nothing is retried silently.
    limits = Limits(research_requests=3, review_requests=2, searches=6, fetches=12, input_tokens=350000,
                    output_tokens=16000, max_candidates=3, max_final_leads=3, transient_retries=0)
    # Worst case per run under these limits; a run is started only if it fits the ceiling.
    case_ceiling = (limits.input_tokens / 1e6 * args.input_usd_per_million
                    + limits.output_tokens / 1e6 * args.output_usd_per_million)
    for case in chosen:
        if args.live and spent + case_ceiling > args.max_usd:
            print(f'Stopping before {case["id"]}: worst-case cost would exceed the ceiling.')
            break
        config = case_config(case, args.config)
        config.limits = limits
        if args.live:
            provider = create_provider(config.ai.provider, config.ai.model, Budget(limits),
                                       api_key=os.environ.get(config.secret_names['ai']),
                                       transport=RecordingTransport(HTTPTransport(), args.output / case['id']))
            provider.preflight()
        else:
            recorded = args.responses / case['id']
            wires = [json.loads((recorded / f'{n}.json').read_text()) for n in ('discovery', 'analysis', 'review')
                     if (recorded / f'{n}.json').exists()]
            provider = create_provider(config.ai.provider, config.ai.model, Budget(limits),
                                       transport=RecordingTransport(FixtureTransport(wires), args.output / case['id']))
        provider.reasoning_effort = config.ai.reasoning_effort
        docs = None
        if case['track'] == 'supplied':
            docs = [source_from_receipt(json.loads((ROOT / 'eval/sources' / f'{s}.json').read_text()), s) for s in case['sources']]
        elif not args.live:
            raise SystemExit(f'{case["id"]}: discovery cases need live retrieval')
        started = time.monotonic()
        row = {'case': case['id'], 'track': case['track']}
        try:
            issue, result = hunt_preview(config, provider, args.output / case['id'], docs=docs, local_date=date.today())
            row.update(leads=len(issue.leads), held=[h['status'] for h in result.held], no_lead_reason=result.no_lead_reason)
        except Exception as exc:  # report run failures as such; never as editorial outcomes
            row.update(run_failure=f'{type(exc).__name__}: {exc}')
        usage = provider.budget.snapshot()
        cost = usage.input_tokens / 1e6 * args.input_usd_per_million + usage.output_tokens / 1e6 * args.output_usd_per_million
        if 'run_failure' in row and (provider.budget.blocked or row['run_failure'].startswith(('ProviderError', 'TransientError', 'BudgetExceeded', 'TimeoutError'))):
            # Provider-side failure: usage may be unknown, so charge the worst case.
            cost = max(cost, case_ceiling)
            row['charged_worst_case'] = True
        row.update(seconds=round(time.monotonic() - started, 1), input_tokens=usage.input_tokens,
                   output_tokens=usage.output_tokens, list_price_usd=round(cost, 4))
        rows.append(row)
        spent += cost
        print(json.dumps(row))
        if 'run_failure' in row or provider.budget.blocked:
            print('Stopping batch: run failure or unknown provider usage.')
            break
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'summary.json').write_text(json.dumps(rows, indent=2) + '\n')


if __name__ == '__main__':
    main()
