# How DocHunt works

DocHunt pairs an AI research agent, which does the judgment work, with a small amount of code that does the parts a reader must be able to trust: links, routes, repeats, recipients, branding and delivery.

## One run

1. **Eligibility.** An hourly check decides whether today's issue is due (see [scheduling](scheduling.md)). Until it is, the check installs nothing beyond Python and incurs no AI or email charges (it uses a few seconds of Actions time).
2. **Prompt.** DocHunt fills its research prompt (`src/dochunt/prompts/hunt.md`) with your `[beat]` section, today's date, and up to 150 leads sent in the last 120 days. Run `dochunt show-prompt` to read it.
3. **Research.** The agent works in two passes, in one session:
   - a *research pass*: it searches across your source families, opens and reads the primary documents, verifies findings and numbers, and finds and verifies the responsible office's contact or submission route on an official page;
   - a *reviewing pass*: acting as a skeptical editor, it checks that each headline and number matches the source, that the records are identifiable, that the route is verified, and that the first step is finished and usable. It revises or drops candidates and keeps at most `max_leads`, best first.

   It returns JSON matching a fixed schema (`src/dochunt/schema/issue.schema.json`).
4. **Checks.** Code checks every lead (below). If anything fails, the agent gets one chance to fix exactly those problems, when enough time is left. Leads that are still broken are dropped, and the rest are sent.
5. **Render.** A fixed, email-safe template renders the issue in your branding, as HTML and plain text, with the AI notice at the top.
6. **Deliver.** The exact rendered email is saved to `data/state.json` before it is sent, then sent with Resend or SMTP. The sent leads are added to `data/leads.jsonl`, so later runs skip them. See [recovery](recovery.md).

Research and delivery run as separate workflow steps. The research step has only the AI keys, and the agent receives only the one it uses; the delivery step has only the email keys, the subscription token (if used) and the Git push token.

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
- turns off the CLI's own user settings, rules, plugins and session history (on your own computer, administrator-managed Claude Code policies still apply);
- stops the agent's whole process tree if the run times out, is cancelled or is interrupted;
- tells it, in the prompt, that text on web pages is information and never instructions.

The agent can't send email or change your repository. It can make web requests, and its prompt includes your newsletter name, beat settings and recent leads. So the worst a misled agent can do is write a bad lead, which the checks and your readers' judgment are there to catch, or reveal that prompt text to a website. Keep anything confidential out of your newsletter name and beat settings.

## The checks

A lead is **dropped** only for problems a reader would trip over:

- a missing or malformed required field; a source, contact or verification link that isn't a valid web address; or an invalid email address or phone number;
- no usable first step: a records request needs a submission link or email; a call-or-email step needs an email, phone or contact page; a download needs a direct link;
- no official page named where the route was verified (records requests and inquiries);
- a source link already sent in an earlier issue (compared after normalizing the address), unless the agent marks the lead as an update with new information;
- two leads on the same document, or more leads than `max_leads` (the agent's ranking is kept);
- a headline over 160 characters.

These produce **warnings** in `check-report.json` but never drop a lead:

- a link that returns an error such as HTTP 404. A 401, 403, 405 or 429 is ignored, because many government sites block automated requests. Redirects aren't followed, and a site that can't be reached produces no warning;
- a number in a headline that doesn't appear in the lead's reporting opportunity or source title.

The agent also lists the pages it says it opened (`checked_sources`). A "no leads" answer must list at least three distinct links; otherwise the run fails and sends nothing, because the likely cause is broken web access rather than a quiet news day.

If every lead is dropped, the email is an honest "no actionable leads" issue. If the first agent session fails or returns something unusable, nothing is sent and the run fails visibly. If the repair session fails, the valid leads from the first answer are sent.

The checks deliberately don't try to re-verify facts against source documents. Earlier versions of DocHunt did, with strict literal-quote and route-wording gates, and they blocked almost every lead. Fact-checking is the agent's reviewing pass and, in the end, the reporter's job: every email says it is AI-generated.

## What each run leaves behind

Runs upload private Actions artifacts kept for 7 days. `document-hunt-RUN_ID` holds whatever the run produced (there is none when nothing was due, and test sends and resends add none); a successful paid preview also uploads `paid-ai-preview-RUN_ID` with the five finished files (no `raw/`), which a test send or resend uses:

- `preview.html`, `preview.txt`: the email;
- `issue.json`: the checked issue that was (or would be) sent;
- `check-report.json`: agent run time and usage, the sources it checked, problems found, dropped leads and why, warnings;
- `preview-manifest.json`: hashes that tie the files to your current configuration;
- `raw/prompt.md`, `raw/agent-output-*.json`: what the agent was first asked and what it returned (never used for sending; not in synthetic previews).

A test send or resend re-checks the manifest's hashes against the files and your current configuration, and re-renders the issue, so an edited artifact or one made with different settings can't be sent. The workflow accepts only a paid preview from an earlier manual run of the same repository and branch.
