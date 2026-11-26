# Adopting updates

Keep each newsroom's private installation under its own control. Review upstream release notes and diffs before adopting a release. A copy made from the template has its own history, so compare two upstream releases instead (for example `https://github.com/OWNER/dochunt/compare/OLD...NEW`), then prepare a branch and pull request in your copy applying the same application, prompt, test and workflow changes. A GitHub administrator can help transfer reviewed files through the web editor; a local development environment is optional.

Preserve `newsroom.toml`, repository secrets, recipients, schedule choices, repository permissions and the entire `data/` directory. Never replace them wholesale with template defaults or another newsroom's state. Changes to the shape of `newsroom.toml` or the state files come with a migration in these notes; follow it rather than replacing files. Review any dependency/action pin, requested permission or secret-scope change.

Keep scheduling disabled during a planned migration if your approved process requires it; document that interruption. Run credential-free CI on the change (the Document Hunt workflow itself runs only on the default branch; pull-request CI renders offline previews), review the exact release commit, then merge only with your installation's required approval. Paid preview, live inbox test and enabling schedules require your own scoped authorization. After a config change, create a fresh preview artifact; older artifacts will fail config consistency checks. Retain state and accepted provider IDs for recovery and rollback. Do not roll state backward merely to roll code back: that can repeat an already accepted email.

## Moving from 0.1 (watch lists) to 0.2 (research agent)

Version 0.2 replaces the fixed watch-list engine with a research agent. `newsroom.toml` changes shape, so an 0.1 file fails validation with `Unknown configuration section` until you migrate it:

1. Set `[schedule] enabled = false` first, and make sure `data/state.json` has no send waiting for recovery. Keep `[identity]` and `[email]` as they are.
2. Replace `[editorial]` with `[beat]`. Move `coverage_brief` to `brief`, keep `geography` and `exclusions`, and add a short `name`. Move the essence of your old watch list and request pages into `source_families` as plain sentences, such as "VA Office of Inspector General reports".
3. Replace `[ai]` with `[agent]`: `runtime = "codex"` with your `OPENAI_API_KEY`, or `runtime = "claude"` with your `ANTHROPIC_API_KEY`.
4. Delete `[hunt]`, `[retrieval]`, `[limits]` and `[[sources]]`. Remove `FIRECRAWL_API_KEY` from your secrets if nothing else uses it.
5. Delete `data/history.jsonl` and commit an empty `data/leads.jsonl` (old history rows have a different shape and can't be used for deduplication, so leads sent under 0.1 may be suggested again). Leave `data/state.json` untouched; it holds your delivery records.

Then run a free `preview` and one `paid-ai-preview` before turning the schedule back on. Start from the closest [example](../examples/).
