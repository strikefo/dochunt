# Adopting updates

Keep each newsroom's private installation under its own control. Review upstream release notes and diffs before adopting a release. In GitHub's website, compare an upstream release against your installation, then prepare a branch/pull request applying the application, prompts, tests and workflow changes. A GitHub administrator can help transfer reviewed files through the web editor; a local development environment is optional.

Preserve `newsroom.toml`, repository secrets, recipients, schedule choices, repository permissions and the entire `data/` directory. Never replace them wholesale with template defaults or another newsroom's state. Schema changes require an explicit documented migration; unsupported versions should fail rather than silently reset history. Review any dependency/action pin, requested permission or secret-scope change.

Keep scheduling disabled during a planned migration if your approved process requires it; document that interruption. Run credential-free CI and offline preview on the change, review the exact release commit, then merge only with your installation's required approval. Paid preview, live inbox test and enabling schedules require your own scoped authorization. After a config change, create a fresh preview artifact; older artifacts will fail config consistency checks. Retain state and accepted provider IDs for recovery and rollback. Do not roll state backward merely to roll code back: that can repeat an already accepted email.

## Moving from 0.1 (watch lists) to 0.2 (research agent)

Version 0.2 replaces the fixed watch-list engine with a research agent. `newsroom.toml` changes shape, so an 0.1 file fails validation with `Unknown configuration section` until you migrate it:

1. Keep `[identity]`, `[email]` and `[schedule]` as they are.
2. Replace `[editorial]` with `[beat]`. Move `coverage_brief` to `brief`, keep `geography` and `exclusions`, and add a short `name`. Move the essence of your old watch list and request pages into `source_families` as plain sentences, such as "VA Office of Inspector General reports".
3. Replace `[ai]` with `[agent]`: `runtime = "codex"` with your `OPENAI_API_KEY`, or `runtime = "claude"` with your `ANTHROPIC_API_KEY`.
4. Delete `[hunt]`, `[retrieval]`, `[limits]` and `[[sources]]`. Remove `FIRECRAWL_API_KEY` from your secrets if nothing else uses it.
5. Delete `data/history.jsonl` and commit an empty `data/leads.jsonl` (old history rows have a different shape and can't be used for deduplication). Leave `data/state.json` untouched; it holds your delivery records.

Then run a free `preview` and one `paid-ai-preview` before turning the schedule back on. Start from the closest [example](../examples/).
