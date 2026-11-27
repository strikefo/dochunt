"""DocHunt command line. Preview never delivers or changes state."""
import argparse
from dataclasses import asdict
from datetime import date, datetime, timezone
import json
import os
import subprocess
from pathlib import Path
from zoneinfo import ZoneInfo
from .config import load_config


def _local_date(config, value=None):
    return date.fromisoformat(value) if value else datetime.now(ZoneInfo(config.schedule.timezone)).date()


def _tracker(state_dir):
    from .state import StateStore
    path = Path(state_dir)
    return StateStore(path, git=False).read()['leads'] if path.exists() else []


def main(argv=None):
    parser = argparse.ArgumentParser(prog='dochunt', description='Daily records-lead emails for a newsroom beat.')
    commands = parser.add_subparsers(dest='command', required=True)

    setup = commands.add_parser('setup', help='Optional temporary local browser wizard')
    setup.add_argument('--repo', type=Path, default=Path('.'))
    setup.add_argument('--no-browser', action='store_true')

    def with_config(p):
        p.add_argument('--config', type=Path, default=Path('newsroom.toml'))
        return p

    with_config(commands.add_parser('validate-config', help='Check newsroom.toml'))
    readiness = with_config(commands.add_parser('delivery-readiness', help='Check email settings and credentials'))
    readiness.add_argument('--state-dir', type=Path, default=Path('data'))

    show = with_config(commands.add_parser('show-prompt', help='Print the exact research prompt the agent would get (free)'))
    show.add_argument('--state-dir', type=Path, default=Path('data'))
    show.add_argument('--date', help='Issue date, YYYY-MM-DD (default: today in the configured timezone)')

    command = with_config(commands.add_parser('preview', help='Write a preview: --synthetic (free, offline) or --paid (the agent runs, billed)'))
    command.add_argument('--output-dir', type=Path, required=True)
    command.add_argument('--state-dir', type=Path, default=Path('data'))
    command.add_argument('--date', help='Issue date, YYYY-MM-DD (default: today in the configured timezone)')
    kind = command.add_mutually_exclusive_group(required=True)
    kind.add_argument('--synthetic', action='store_true', help='Fictional offline issue; no AI calls (free)')
    kind.add_argument('--paid', action='store_true', help='Run the research agent; billed to your AI account')
    command.add_argument('--fixture', choices=('sample', 'no-leads'), default='sample', help='Synthetic issue to render')

    for name in ('due-check', 'run'):
        command = with_config(commands.add_parser(name))
        command.add_argument('--state-dir', type=Path, default=Path('data'))
        command.add_argument('--repo', type=Path, default=Path('.'))
        command.add_argument('--manual', action='store_true')
        command.add_argument('--branch', default='main')
        if name == 'run':
            command.add_argument('--output-dir', type=Path, default=Path('output'))
            stages = command.add_mutually_exclusive_group()
            stages.add_argument('--research-only', action='store_true')
            stages.add_argument('--delivery-only', action='store_true')
    for name in ('deliver', 'resend', 'preview-test'):
        delivery = with_config(commands.add_parser(name))
        delivery.add_argument('--issue-dir', type=Path, required=True)
        delivery.add_argument('--state-dir', type=Path, default=Path('data'))
        delivery.add_argument('--repo', type=Path, default=Path('.'))
        delivery.add_argument('--branch', default='main')
        if name in ('resend', 'preview-test'):
            delivery.add_argument('--attempt-id', required=True)
        if name == 'preview-test':
            delivery.add_argument('--recipient', required=True)
    download = with_config(commands.add_parser('download-preview'))
    download.add_argument('--run-id', required=True)
    download.add_argument('--repository', required=True)
    download.add_argument('--branch', required=True)
    download.add_argument('--output-dir', type=Path, required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == 'setup':
            from .setup_server import launch
            launch(args.repo, open_browser=not args.no_browser)
            return 0
        config = load_config(args.config)
        if args.command == 'validate-config':
            print(f'Configuration valid: {config.agent.runtime} agent ({config.model}), '
                  f'up to {config.beat.max_leads} leads, scheduling {"enabled" if config.schedule.enabled else "disabled"}.')
        elif args.command == 'delivery-readiness':
            from .delivery import delivery_readiness, sender_header
            from .state import StateStore
            states = StateStore(args.state_dir, git=False).read()
            pending = [r['prepared'] for r in states['attempts'].values() if not r['prepared']['preview_test'] and r['status'] != 'accepted']
            if pending:
                sender_header(pending[0]['sender'], pending[0].get('sender_name', ''))
            else:
                sender_header(config.email.sender, config.identity.sender_name)
            delivery_readiness(pending[0]['provider'] if pending else config.email.provider)
            if not pending:
                from .runtime import subscription_readiness
                subscription_readiness(config)
        elif args.command == 'show-prompt':
            from .prompt import hunt_prompt
            print(hunt_prompt(config, _local_date(config, args.date), _tracker(args.state_dir)))
        elif args.command == 'preview':
            from .hunt import preview
            preview(config, args.output_dir, local_date=_local_date(config, args.date), tracker_rows=_tracker(args.state_dir),
                    synthetic=args.synthetic, fixture_name=args.fixture)
            print(f'Preview written to {args.output_dir}; no email sent.')
        elif args.command == 'download-preview':
            from .artifacts import download_preview, load_preview
            download_preview(args.run_id, args.repository, args.branch, args.output_dir)
            load_preview(args.output_dir, config, require_paid=True)
        elif args.command == 'due-check':
            from .schedule import due
            from .state import StateStore
            decision = due(config, datetime.now(timezone.utc), StateStore(args.state_dir, repo=args.repo).read(), manual=args.manual)
            print(json.dumps(asdict(decision)))
            if os.environ.get('GITHUB_OUTPUT'):
                with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
                    stream.write(f'due={str(decision.due).lower()}\n')
        elif args.command == 'run':
            from .runtime import run
            from .state import PublishedStateStore, StateStore
            # Research reads state only; it needs no Git credentials. Delivery publishes state to the branch.
            store = (StateStore(args.state_dir, repo=args.repo, git=False) if args.research_only
                     else PublishedStateStore(args.state_dir, repo=args.repo, branch=args.branch))
            outcome = run(config, store, args.output_dir, manual=args.manual,
                          research_only=args.research_only, delivery_only=args.delivery_only)
            print(outcome.reason if hasattr(outcome, 'reason') else
                  f'Delivery state: {outcome.state.value}; provider acceptance does not confirm inbox delivery.')
        elif args.command in ('deliver', 'resend', 'preview-test'):
            from .delivery import prepare, send
            from .state import PublishedStateStore
            from .artifacts import load_preview
            # Only a paid preview can be sent: a synthetic one is fictional, even for a single test address.
            issue, rendered = load_preview(args.issue_dir, config, require_paid=True)
            prepared = prepare(issue, rendered, config, attempt_id=getattr(args, 'attempt_id', None), recipient=getattr(args, 'recipient', None))
            from .runtime import recheck_for
            receipt = send(prepared, PublishedStateStore(args.state_dir, repo=args.repo, branch=args.branch),
                           recheck=recheck_for(config), repeat=args.command == 'resend', match_saved=True)
            print(f'Delivery state: {receipt.state.value}; provider acceptance does not confirm inbox delivery.')
    except subprocess.CalledProcessError as exc:
        parser.exit(2, f'dochunt {args.command} failed: a git command exited with status {exc.returncode}.\n')
    except (ValueError, OSError, KeyError, RuntimeError) as exc:
        # Messages are application-owned and never include secret values.
        parser.exit(2, f'dochunt {args.command} failed: {exc}\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
