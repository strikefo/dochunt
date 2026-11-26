# How DocHunt works

DocHunt pairs an AI research agent, which does the judgment work, with a small amount of code that does the parts a reader must be able to trust: links, routes, repeats, recipients, branding and delivery.

## One run

1. **Eligibility.** An hourly check decides whether today's issue is due (see [scheduling](scheduling.md)). Nothing is installed or billed until it is.
2. **Prompt.** DocHunt fills its research prompt (`src/dochunt/prompts/hunt.md`) with your `[beat]` section, today's date, and the leads sent in the last 120 days. Run `dochunt show-prompt` to read it.
3. **Research.** The agent works in two passes, in one session:
   - a *research pass*: it searches across your source families, opens and reads the primary documents, verifies findings and numbers, and finds and verifies the responsible office's contact or submission route on an official page;
   - a *reviewing pass*: acting as a skeptical editor, it checks that each headline and number matches the source, that the records are identifiable, that the route is verified, and that the first step is finished and usable. It revises or drops candidates and keeps at most `max_leads`, best first.

   It returns JSON matching a fixed schema (`src/dochunt/schema/issue.schema.json`).
4. **Checks.** Code checks every lead (below). If anything fails, the agent gets one chance to fix exactly those problems. Leads that are still broken are dropped, and the rest are sent.
5. **Render.** A fixed, email-safe template renders the issue in your branding, as HTML and plain text, with the AI notice at the top.
6. **Deliver.** The exact rendered email is saved to `data/state.json` before it is sent, then sent with Resend or SMTP. The sent leads are added to `data/leads.jsonl`, so later runs skip them. See [recovery](recovery.md).

Research and delivery run as separate workflow steps. The research step has only the AI key; the delivery step has only the email keys and the Git push token.

## The agent

| | Codex CLI | Claude Code |
|---|---|---|
| Key | `OPENAI_API_KEY` | `ANTHROPIC_API_KEY` |
| Web access | built-in web search | `WebSearch` and `WebFetch` tools only |
| Shell and file tools | shell, exec, apps, plugins, browser and computer use turned off; read-only sandbox as a backstop. Codex's code-mode host stays on because web search runs through it; it has no file or network access of its own | none |
| Output | `--output-schema` | `--json-schema` |
| Spending stop | none; use account limits | `max_budget_usd` per session |

For both, DocHunt:

- runs the CLI in an empty temporary folder, not in your repository;
- passes it an environment with only its own API key and basic system settings, with no email credentials, no GitHub token and no recipients;
- turns off the CLI's own user settings, rules, plugins and session history;
- stops the agent's whole process tree if the run times out, is cancelled or is interrupted;
- tells it, in the prompt, that text on web pages is information and never instructions.

The agent can't send email or change your repository. The worst a misled agent can do is write a bad lead, which the checks and your readers' judgment are there to catch.

## The checks

A lead is **dropped** only for problems a reader would trip over:

- a source, contact or verification link that isn't a valid web address, or an invalid email address or phone number;
- no usable first step: a records request needs a submission link or email; a call-or-email step needs an email, phone or contact page; a download needs a direct link;
- no official page named where the route was verified (records requests and inquiries);
- a source document already sent in an earlier issue, unless the agent marks the lead as an update with new information;
- two leads on the same document, or more leads than `max_leads` (the agent's ranking is kept);
- a headline over 160 characters.

These produce **warnings** in `check-report.json` but never drop a lead:

- a link that returns an error such as HTTP 404. A 403, 401 or 429 is ignored, because many government sites block automated requests;
- a number in a headline that doesn't appear in the lead's own text.

The agent also lists the pages it actually opened (`checked_sources`). A "no leads" answer must show at least three; otherwise the run fails and sends nothing, because the likely cause is broken web access rather than a quiet news day.

If every lead is dropped, the email is an honest "no actionable leads" issue. If the agent fails or returns something unusable twice, nothing is sent and the run fails visibly.

The checks deliberately don't try to re-verify facts against source documents. Earlier versions of DocHunt did, with strict literal-quote and route-wording gates, and they blocked almost every lead. Fact-checking is the agent's reviewing pass and, in the end, the reporter's job: every email says it is AI-generated.

## What each run leaves behind

Every scheduled or previewed run uploads a private Actions artifact for 7 days:

- `preview.html`, `preview.txt`: the email;
- `issue.json`: the checked issue that was (or would be) sent;
- `check-report.json`: agent run time and usage, the sources it checked, problems found, dropped leads and why, warnings;
- `preview-manifest.json`: hashes that tie the files to your current configuration;
- `raw/prompt.md`, `raw/agent-output-*.json`: what the agent was asked and returned (never used for sending).

A test send or resend re-checks the manifest and re-renders the issue, so an edited or stale artifact can't be sent.
