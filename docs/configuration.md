# Configuration

Everything lives in `newsroom.toml`. Credentials never go in this file. They go in GitHub Actions secrets (see [setup](setup.md)). Check a file without network or cost:

```bash
dochunt validate-config --config newsroom.toml
```

## Sections

| Section | What it sets |
|---|---|
| `[identity]` | `id` (choose once; it is part of every issue's identity), `newsletter_name`, `sender_name`, `accent_color` |
| `[editorial]` | `coverage_brief` (what you cover, in plain words), `geography`, `exclusions`, `max_final_leads` |
| `[ai]` | `provider` (`openai` or `anthropic`), `model`, `reasoning_effort` (`low` to `max`, default `medium`), optional `reviewer_model` |
| `[email]` | `provider` (`resend` or `smtp`), `sender`, `recipients`, and for SMTP `smtp_host`/`smtp_port` |
| `[schedule]` | `enabled` (keep `false` until you've reviewed previews), `timezone`, `weekdays` (Monday=0), `preferred_time` |
| `[retrieval]` | `firecrawl_enabled`, `firecrawl_max_calls`, `firecrawl_timeout_seconds` |
| `[hunt]` | what to watch and where requests go (below) |
| `[limits]` | per-run ceilings (see [costs and limits](costs-and-limits.md)) |

## `[hunt]`

| Key | Default | Meaning |
|---|---|---|
| `mode` | `"watch"` | `watch`: new documents on the watch list. `search`: open web search from the brief. |
| `watch` | `[]` | Feeds (RSS/Atom) or listing pages to check for new documents. Feeds work best. |
| `request_pages` | `[]` | FOIA or public-records pages for the agencies you cover. Requests can only be routed to places these pages show. |
| `keywords` | `[]` | If set, a feed item's title must contain one of these words to be opened. Use this for broad feeds like GAO's. |
| `playbook` | `""` | Path to a beat playbook. Its `watch`, `request_pages` and `watch_keywords` are added to yours. |
| `window_days` | `14` | How recent a document must be to count as new. |
| `max_new_documents` | `5` | New documents analysed per run. Each costs one analysis call, plus a review if it produces leads. |
| `max_source_chars` | `240000` | Character allowance for the sources in one analysis. A longer document keeps its beginning and end. |
| `allowed_acronyms` | `[]` | Acronyms your readers know and headlines may use unexplained, for example `["VA", "DOD"]`. |
| `seen_path` | `data/seen-documents.json` | Used only by local library calls that ask to record documents. Scheduled runs remember documents in `data/state.json`. Previews never record them, so a preview may re-read documents an earlier email already covered. |

Watch mode refuses to run with nothing to watch (set `watch` or a playbook). `validate-config` does not check this; the first preview does.

**Building a good watch list** is most of the setup work, and it matters more than any other setting.

- Prefer feeds. Many agencies, auditors, inspectors general, and the Federal Register (by agency) publish RSS.
- Add the request pages for each agency whose documents you watch. A lead without a confirmable route lands in "Check the request route first".
- Run a paid preview and read `engine-result.json` and `analysed-documents.json` to see what was found, skipped and held.

## Playbooks

A playbook is an editable beat guide. It holds:

- record families (what records exist, who holds them, where to start);
- useful comparisons;
- reasons to reject an idea;
- optionally `watch`, `request_pages` and `watch_keywords`.

It contains no stories. The starter [labor, military and politics playbooks](../playbooks/README.md) are written from general knowledge. **Their URLs must be checked** before you rely on them.
