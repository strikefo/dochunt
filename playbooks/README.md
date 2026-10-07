# Starter playbooks

Optional, editable beat guides for the evidence engine (`[hunt] playbook = "playbooks/labor.toml"`).
Leave `playbook` empty to use only the newsroom's own coverage brief.

A playbook lists **record families** (what records exist, what they can reveal, who usually
holds them, where to start), useful **comparisons**, and reasons to **reject** an idea. It
contains no preselected stories and does not promise that a run produces a lead.

Source URLs are starting points DocHunt may fetch and read. They are never treated as verified
access: a route counts only when a fetched page shows it. The starter URLs were written from
general knowledge on 2026-10-06 and have not been checked; a newsroom should replace them with
its own state and local sources.

Only the military playbook has been used in live runs (watch mode; see `docs/evaluation.md`).
The labor and politics playbooks have not. Whether playbooks improve discovery is what
discovery cases D1 and D2 in `eval/cases.toml` are designed to test.

## Watch mode

With `[hunt] mode = "watch"`, DocHunt does not search the web. Each run reads the playbook's `watch` pages, plus any in `[hunt] watch`, and keeps documents published in the last `window_days` (default 14) that it has not reported before. It then drafts follow-on requests using the `request_pages`. Feeds (RSS/Atom) give exact dates. On ordinary listing pages, DocHunt opens a few linked documents and reads each one's own publication date. A document without a date inside the window is skipped, never treated as new. The starter `watch` and `request_pages` URLs are unverified and must be checked by the newsroom.
