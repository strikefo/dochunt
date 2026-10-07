# Fixed DocHunt evaluation

- [`cases.toml`](cases.toml): 15 supplied-source cases (5 regression, 10 never used as cases)
  and 6 held-out discovery briefs across labor, education, housing/local government,
  politics and military.
- [`criteria.md`](criteria.md): scoring rubric, outcome classes and proposed thresholds.
- [`sources/`](sources): the saved public receipts for supplied cases, copied unchanged from
  earlier runs and fixtures, with SHA-256 hashes in `sources/MANIFEST.json`.

Frozen on 2026-10-06 in a commit that precedes the engine and prompts it evaluates.

## Honest limits

- **Not blind.** Every supplied receipt appeared somewhere in earlier development
  (production discovery runs or test fixtures). The person who wrote the engine prompts
  selected these cases and skimmed their opening text to record the `exercises` tags.
  No expected answers were written.
- **Saved, not fresh.** Receipts show what was retrievable on their original dates. Some
  fixture-origin receipts may be partial excerpts; `MANIFEST.json` records size and whether
  text hit the old 120,000-character acquisition cap (only `gao-26-107722` and
  `ca-auditor-2025-128` did; the GAO cut removed only footer text, the California cut
  removed late pages).
- **The corpus is audit-heavy.** Nearly every saved receipt is an audit or a FOIA page,
  because earlier discovery steered there. That bias is itself a finding; the discovery
  track and playbooks are meant to test whether DocHunt can find records beyond audits.
- **Discovery cases need live retrieval and model calls**, which cost money. They are
  frozen here so they cannot be chosen after the engine is tuned.

## Running offline

`python tools/run_eval.py --case R3 --responses RECORDED_DIR --output out/eval` replays one
supplied case through the engine from recorded provider responses (`RECORDED_DIR/<case>/analysis.json`
and `review.json`); no network calls are possible in that mode. A live run
(`--live`) uses your own API key and requires a `--max-usd` ceiling. Results so far are
summarized in [docs/evaluation.md](../docs/evaluation.md).
