# Configuration

Everything lives in `newsroom.toml`. Credentials never go in this file. They go in GitHub Actions secrets (see [setup](setup.md)). Check a file, and see exactly what the agent will be asked, without network or cost:

```bash
dochunt validate-config --config newsroom.toml
```

```bash
dochunt show-prompt --config newsroom.toml
```

## Sections

| Section | What it sets |
|---|---|
| `[identity]` | `id` (choose once; it is part of every issue's identity), `newsletter_name`, `sender_name`, `accent_color` (six-digit hex, default `#152b44`) |
| `[beat]` | what to hunt for (below) |
| `[agent]` | which research agent runs, and its limits (below) |
| `[email]` | `provider` (`resend` or `smtp`), `sender`, `recipients` (required, even with a subscription list), and for SMTP `smtp_host`/`smtp_port` (default 587) |
| `[schedule]` | `enabled` (default `false`; keep it until you've reviewed previews), `timezone` (required, such as `America/Chicago`), `weekdays` (Monday=0; default Monday-Friday), `preferred_time` (`HH:MM`, default `08:00`), `window_hours` (1-18, default 12) |

All five sections are required. Unknown sections or keys are rejected, so a typo can't be silently ignored.

## `[beat]`

This section is the newsroom's whole editorial voice. It goes into the agent's prompt as written.

| Key | Default | Meaning |
|---|---|---|
| `name` | required | A short name for the beat, such as `"Military, defense and veterans"`. |
| `brief` | required | What you cover and what you want from the documents, in plain language. Multi-line text is fine. |
| `priorities` | `[]` | Topics in order of importance, one per entry. The agent ranks leads by these. |
| `source_families` | `[]` | The kinds of sources to check, one per entry: oversight offices, dockets, data portals, committees, procurement sites. Name specific agencies and sites. This replaces the old watch list. |
| `geography` | `"United States"` | Where the beat is. |
| `exclusions` | `""` | What never qualifies. |
| `records_law` | federal FOIA | The law request drafts cite, such as `"the Wisconsin Public Records Law (Wis. Stat. 19.31-19.39)"`. News-media and fee-waiver language is used only where that law provides for it. |
| `freshness_days` | `3` | Emphasize documents published or updated in this many days (1-30). Older material qualifies only when something new makes it actionable. |
| `max_leads` | `3` | The most leads in one email (1-5). The agent includes fewer when fewer qualify. |
| `extra_instructions` | `""` | Anything else, in plain language. It is added to the prompt under "Newsroom instructions". |

### Writing a good beat

- Be specific in `source_families`. "VA Office of Inspector General reports" is better than "watchdogs". Name the state auditor, the city's inspector general, the court docket system.
- Put what your reporters care about most first in `priorities`. The military example's first priority is "oversight and money".
- Read `raw/prompt.md` and the email from a paid preview, then adjust. Most improvement comes from the beat section.

## `[agent]`

| Key | Default | Meaning |
|---|---|---|
| `runtime` | required | `"codex"` (Codex CLI, needs `OPENAI_API_KEY`) or `"claude"` (Claude Code, needs `ANTHROPIC_API_KEY`). |
| `model` | `gpt-6.1-sol` / `claude-opus-5-5` | The model the agent uses. Any model your API account can use with that CLI. |
| `reasoning_effort` | `"high"` | `low`, `medium`, `high`, `xhigh` or `max`. Higher is slower and costs more. Codex treats `max` as `xhigh`. |
| `max_budget_usd` | `5.0` | Claude Code only: the CLI's spending stop for each agent session (0.5-50). It is the CLI's own estimate and can overshoot slightly. |
| `timeout_minutes` | `18` | Each agent session's time limit (5-20). The run as a whole must finish within the workflow's 25-minute deadline. |

The workflow installs a pinned version of the CLI you choose. See [how it works](how-it-works.md#the-agent) for what the agent can and can't do.

## `[email]` and `[schedule]`

Recipients come only from your configuration or your own subscription list, never from the agent, which never sees them. `send-preview-test` can send only to an address in `[email] recipients`.

### Subscription list (optional)

Instead of a fixed list, readers can manage their own subscription through a small service you run, such as a Cloudflare Worker with an opt-in/opt-out page.

| Key | Meaning |
|---|---|
| `subscription_url` | The service's https origin, such as `"https://subscriptions.example.org"` (no path). |
| `subscription_domains` | Domains subscribers may use, such as `["newsroom.example"]`; use your own domain. Addresses in `recipients` are always allowed too (for an editor's personal address). |

When it is set, every email (previews included) carries a "Manage subscription" link to `<subscription_url>/`, and each send reads the list just before sending: `GET <subscription_url>/recipients` with `Authorization: Bearer <SUBSCRIPTION_API_TOKEN>` must return `{"recipients": [...], "manage_url": "<subscription_url>/"}`, with exactly that link. The issue goes to that list; `recipients` is then used only for test sends and as allowed exceptions.

- Before research in a scheduled or `deliver` run, the run fetches and checks the list, so a missing or revoked token or an outage stops it without spending on research. A `paid-ai-preview` skips this check.
- If the list can't be loaded at send time, is empty, has more than 500 distinct addresses, or contains an address outside the allowed domains that isn't listed in `recipients`, the email is not sent and the run fails visibly. There is never a fallback to `recipients`. Domains match exactly, so a subdomain needs its own entry.
- Test sends (`send-preview-test`) go only to an address in `recipients` and don't read the list.
- If a send has to be retried, the list it was sent to is checked again first. If anyone in the saved send has since opted out, or that list is no longer configured, the retry stops for you to reconcile.
- Redirects are not followed.

## Email privacy and limits

Readers never see each other's addresses. With Resend, each recipient gets an individually addressed copy, all in one batch request; with SMTP, one message goes to all recipients as hidden recipients, with `undisclosed-recipients:;` as its To header. Nobody outside the recipient list gets a copy. Resend's batch request takes at most 100 recipients, and a larger list stops the send (before research in a scheduled or `deliver` run; use SMTP for bigger lists).

The schedule runs on `weekdays` (0 is Monday) at or after `preferred_time` in `timezone`, within `window_hours` (default 12). GitHub can delay or skip scheduled checks, so for on-time email add an external timer. See [scheduling](scheduling.md).
