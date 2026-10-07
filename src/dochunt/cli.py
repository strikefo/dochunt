"""Configuration and preview CLI. Preview has no delivery or state mutation."""
import argparse
from dataclasses import asdict
from datetime import datetime, date
from importlib.resources import files
import json
import os
import subprocess
from pathlib import Path
from zoneinfo import ZoneInfo
from .config import load_config, validate_model_configuration, ModelConfigurationError
from .budget import Budget, BudgetExceeded
from .providers import create_provider
from .providers.base import ProviderError, CapabilityError
from .render import render_issue


def synthetic_provider(config):
    """Offline provider replaying bundled fictional outputs; no network, no key."""
    from .providers.base import FixtureTransport
    data=json.loads(files('dochunt').joinpath('fixtures','fictional_preview.json').read_text())
    wires=[{'status':'completed','usage':{'input_tokens':0,'output_tokens':0},
            'output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(o),'annotations':[]}]}]} for o in data['outputs']]
    from .providers.openai import OpenAIProvider
    return OpenAIProvider('gpt-6.1-sol',Budget(config.limits),FixtureTransport(wires)),data['sources']


def engine_preview(config,output_dir,*,provider=None,local_date=None,seen=None,docs=None,synthetic=False):
    """Evidence-engine preview: the email, its issue, the engine result, the exact source
    texts and the documents analysed. Raw model outputs go to raw/. No delivery."""
    from .engine import hunt_preview
    if provider is None:
        provider=create_provider(config.ai.provider,config.ai.model,Budget(config.limits),api_key=os.environ.get(config.secret_names['ai']))
    provider.reasoning_effort=config.ai.reasoning_effort
    if not synthetic and config.ai.reviewer_model and getattr(provider,'reviewer',None) is None:
        # The independent review call uses its own model on the same account and shared budget.
        provider.reviewer=create_provider(config.ai.provider,config.ai.reviewer_model,provider.budget,
                                          api_key=os.environ.get(config.secret_names['ai']))
        provider.reviewer.reasoning_effort=config.ai.reasoning_effort
    if not synthetic:
        provider.preflight()
        if getattr(provider,'reviewer',None) is not None: provider.reviewer.preflight()
    output_dir=Path(output_dir)
    day=local_date or datetime.now(ZoneInfo(config.schedule.timezone)).date()
    issue,result=hunt_preview(config,provider,output_dir,docs=docs,local_date=day,seen=seen,raw_dir=output_dir/'raw')
    (output_dir/'preview.txt').unlink(missing_ok=True); (output_dir/'preview.html').unlink(missing_ok=True)
    rendered=render_issue(issue,config)
    (output_dir/'preview.html').write_text(rendered.html,encoding='utf-8')
    (output_dir/'preview.txt').write_text(rendered.text,encoding='utf-8')
    data=asdict(issue); data['local_date']=issue.local_date.isoformat(); data['fingerprint']=rendered.fingerprint
    (output_dir/'issue.json').write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    from .artifacts import write_manifest, ENGINE_FILES
    write_manifest(output_dir,config,synthetic,ENGINE_FILES)
    return rendered


def preview(config,output_dir,*,provider=None,synthetic=False,history_path=None,local_date=None,seen=None,sources=None):
    """Write a preview (no delivery). Synthetic: a fictional run through the real engine,
    gates and renderer, for setup checks. Otherwise a paid run with the configured provider."""
    if synthetic and provider is not None: raise ValueError('Choose synthetic or provider preview')
    from .engine import SourceDoc, source_from_receipt
    docs=None
    if synthetic:
        provider,raw=synthetic_provider(config)
        docs=[SourceDoc(**d) for d in raw]
    elif sources is not None:
        docs=[source_from_receipt(r,f's{i}') for i,r in enumerate(sources)]
    return engine_preview(config,output_dir,provider=provider,local_date=local_date,seen=seen,docs=docs,synthetic=synthetic)


def main(argv=None):
    parser=argparse.ArgumentParser(prog='dochunt')
    commands=parser.add_subparsers(dest='command',required=True)
    setup=commands.add_parser('setup',help='Optional temporary local browser wizard')
    setup.add_argument('--repo',type=Path,default=Path('.'))
    setup.add_argument('--no-browser',action='store_true')
    readiness=commands.add_parser('delivery-readiness')
    readiness.add_argument('--config',type=Path,default=Path('newsroom.toml'))
    readiness.add_argument('--state-dir',type=Path,default=Path('data'))
    validate=commands.add_parser('validate-config')
    validate.add_argument('--config',type=Path,default=Path('newsroom.toml'))
    command=commands.add_parser('preview')
    command.add_argument('--config',type=Path,default=Path('newsroom.toml'))
    command.add_argument('--output-dir',type=Path,required=True)
    command.add_argument('--history',type=Path)
    command.add_argument('--synthetic',action='store_true',help='Fictional offline run through the real engine; no provider requests')
    command.add_argument('--sources',type=Path,help='Saved source receipts (JSON list) to analyse instead of discovery or the watch list')
    for name in ('due-check','run'):
        command=commands.add_parser(name)
        command.add_argument('--config',type=Path,default=Path('newsroom.toml'))
        command.add_argument('--state-dir',type=Path,default=Path('data'))
        command.add_argument('--repo',type=Path,default=Path('.'))
        command.add_argument('--manual',action='store_true')
        command.add_argument('--branch',default='main')
        if name=='run':
            command.add_argument('--output-dir',type=Path,default=Path('output'))
            stages=command.add_mutually_exclusive_group()
            stages.add_argument('--research-only',action='store_true')
            stages.add_argument('--delivery-only',action='store_true')
    for name in ('deliver','resend','preview-test'):
        delivery=commands.add_parser(name)
        delivery.add_argument('--config',type=Path,default=Path('newsroom.toml'))
        delivery.add_argument('--issue-dir',type=Path,required=True)
        delivery.add_argument('--state-dir',type=Path,default=Path('data'))
        delivery.add_argument('--repo',type=Path,default=Path('.'))
        delivery.add_argument('--branch',default='main')
        if name in ('resend','preview-test'): delivery.add_argument('--attempt-id',required=True)
        if name=='preview-test': delivery.add_argument('--recipient',required=True)
    download=commands.add_parser('download-preview')
    download.add_argument('--run-id',required=True)
    download.add_argument('--repository',required=True)
    download.add_argument('--branch',required=True)
    download.add_argument('--output-dir',type=Path,required=True)
    download.add_argument('--config',type=Path,default=Path('newsroom.toml'))
    args=parser.parse_args(argv)
    try:
        if args.command=='setup':
            from .setup_server import launch
            launch(args.repo,open_browser=not args.no_browser)
            return 0
        config=load_config(args.config)
        if args.command=='delivery-readiness':
            from .delivery import delivery_readiness, sender_header
            from .state import StateStore
            states=StateStore(args.state_dir,git=False).read()
            pending=[r['prepared'] for r in states['attempts'].values() if not r['prepared']['preview_test'] and r['status']!='accepted']
            if pending:
                sender_header(pending[0]['sender'],pending[0].get('sender_name',''))
            else:
                sender_header(config.email.sender,config.identity.sender_name)
            delivery_readiness(pending[0]['provider'] if pending else config.email.provider)
        elif args.command=='download-preview':
            from .artifacts import download_preview, load_preview
            download_preview(args.run_id,args.repository,args.branch,args.output_dir)
            load_preview(args.output_dir,config,require_paid=True)
        elif args.command=='due-check':
            from .schedule import due
            from .state import StateStore
            from datetime import timezone
            decision=due(config,datetime.now(timezone.utc),StateStore(args.state_dir,repo=args.repo).read(),manual=args.manual)
            print(json.dumps(asdict(decision)))
            if os.environ.get('GITHUB_OUTPUT'):
                with open(os.environ['GITHUB_OUTPUT'],'a') as stream: stream.write(f'due={str(decision.due).lower()}\n')
        elif args.command=='run':
            from .runtime import run
            from .state import PublishedStateStore
            outcome=run(config,PublishedStateStore(args.state_dir,repo=args.repo,branch=args.branch),args.output_dir,manual=args.manual,research_only=args.research_only,delivery_only=args.delivery_only)
            print('Runtime finished; provider acceptance is not inbox delivery.' if not hasattr(outcome,'reason') else outcome.reason)
        elif args.command=='preview':
            sources=json.loads(args.sources.read_text()) if args.sources else None
            preview(config,args.output_dir,synthetic=args.synthetic,history_path=args.history,sources=sources)
            print('Synthetic preview written.' if args.synthetic else 'Preview written; no delivery attempted.')
        elif args.command in ('deliver','resend','preview-test'):
            from .delivery import prepare, send
            from .state import PublishedStateStore
            from .render import RenderedIssue
            from .artifacts import load_preview
            issue,rendered=load_preview(args.issue_dir,config,require_paid=args.command=='preview-test',require_send=True)
            analysed=args.issue_dir/'analysed-documents.json'
            seen=tuple(json.loads(analysed.read_text())) if analysed.exists() and args.command!='preview-test' else ()
            prepared=prepare(issue,rendered,config,attempt_id=getattr(args,'attempt_id',None),recipient=getattr(args,'recipient',None),seen_documents=seen)
            receipt=send(prepared,PublishedStateStore(args.state_dir,repo=args.repo,branch=args.branch))
            print(f'Delivery state: {receipt.state.value}; provider acceptance does not confirm inbox delivery.')
        else:
            validate_model_configuration(config)
            print(f'Configuration valid; scheduling {"enabled" if config.schedule.enabled else "disabled"}.')
    except ModelConfigurationError as exc:
        parser.exit(2,str(exc)+'\n')
    except (ValueError,OSError,ProviderError,CapabilityError,BudgetExceeded,KeyError,RuntimeError,subprocess.CalledProcessError):
        parser.exit(2,'Configuration or preview failed; check settings, source coverage and provider availability.\n')
    return 0

if __name__ == '__main__': raise SystemExit(main())
