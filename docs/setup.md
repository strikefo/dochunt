# Setup

You need:

- a GitHub account that can create **private** repositories and use Actions;
- an API account with billing at OpenAI (for the Codex agent) or Anthropic (for the Claude Code agent). A ChatGPT or Claude chat subscription does not include API access;
- an email sending account on a domain you control (Resend, or any SMTP provider with STARTTLS).

No local software is required. Everything below can be done on GitHub's website.

## 1. Make your private copy

Choose **Use this template → Create a new repository** and select **Private**. Don't fork instead: a fork of a public repository stays public, and your copy will hold your recipients. Enable Actions when GitHub asks. Never make your copy public: it will contain your recipients, your sent emails and your list of sent leads. The workflow refuses to run in a public repository. Don't turn on GitHub Pages for it either: Pages can serve the repository's files publicly even from a private repository, and `newsroom.toml` and `data/state.json` hold your recipients and email content.

## 2. Edit `newsroom.toml`

Edit it in GitHub's editor and commit to the default branch. Start from the closest example ([military](../examples/military.toml), [education](../examples/education.toml), [labor](../examples/labor.toml), [local government](../examples/local-government.toml)). Then set:

- **`[identity]`**: a newsroom `id` you won't change, the newsletter name, sender name and accent color;
- **`[beat]`**: the most important section. Describe your beat in plain language, list your priorities and the kinds of sources to check, and name the records law to cite. See [configuration](configuration.md#beat);
- **`[agent]`**: `runtime = "codex"` or `"claude"`, and optionally a model;
- **`[email]`**: `provider`, `sender` and `recipients`;
- **`[schedule]`**: your IANA `timezone` (such as `America/Chicago`), with `enabled = false` for now.

## 3. Add secrets

Go to **Settings → Secrets and variables → Actions → New repository secret** and add:

| Secret | When |
|---|---|
| `OPENAI_API_KEY` | `agent.runtime = "codex"` |
| `ANTHROPIC_API_KEY` | `agent.runtime = "claude"` |
| `RESEND_API_KEY` | `email.provider = "resend"` |
| `SMTP_USERNAME`, `SMTP_PASSWORD` | `email.provider = "smtp"` |
| `SUBSCRIPTION_API_TOKEN` | `email.subscription_url` is set ([subscription list](configuration.md#subscription-list-optional)) |

Never put a key in `newsroom.toml`, in a workflow input or in an issue.

If a branch ruleset protects your default branch, DocHunt's state commits need a way through. Create a GitHub App that is the ruleset's bypass actor and has contents write access, install it on the repository, then add the repository variable `BOT_APP_CLIENT_ID` and the secret `BOT_APP_PRIVATE_KEY`. Without them, DocHunt pushes with the workflow's own token.

## 4. Preview, then test, then schedule

Use **Actions → Document Hunt → Run workflow** on the default branch:

1. **`preview`** (free). A fictional issue rendered with your branding. Download the `document-hunt-RUN_ID` artifact and open `preview.html`. This only proves that your settings render.
2. **`paid-ai-preview`** (billed). A real agent run on your beat. It sends no email and changes no state. Download `paid-ai-preview-RUN_ID` and read:
   - `preview.html` and `preview.txt`: the email;
   - `check-report.json`: run time, token usage (and cost, for Claude), problems the checks found, any leads dropped and why, and link warnings.

   The `document-hunt-RUN_ID` artifact also has `raw/prompt.md` (exactly what the agent was asked) and `raw/agent-output-*.json` (what it returned). Expect to adjust your `[beat]` section a few times.
3. **`send-preview-test`**. Sends that exact preview to **one** address from your recipients list. Enter the paid preview's run ID, the recipient, and a new `attempt_id` (letters, digits, `_` and `-`). Check the inbox, spam folder, links and the layout on a phone.
4. **Schedule.** When you are satisfied, set `[schedule] enabled = true`. An hourly check (at minute 17 UTC) starts a run after your `preferred_time` on your `weekdays`, within `window_hours` (default 12). GitHub starts these checks only a few times a day, so the email can arrive hours late; for on-time email, add an external timer ([scheduling](scheduling.md#githubs-schedule-is-sparse-use-an-external-timer-for-on-time-email)). Missed days are not backfilled.

Other modes:

- `deliver` runs today's issue now and sends it to everyone.
- `resend` re-sends a chosen paid preview to everyone, under a new attempt ID.

See [scheduling](scheduling.md) and [recovery](recovery.md).

## Email domain

**Resend.** Add and verify your sending domain in Resend and install the DNS records it gives you. Use a sender address at that domain and a key scoped to sending. See [Resend domains](https://resend.com/docs/dashboard/domains/introduction).

**SMTP.** Set `email.provider = "smtp"`, `smtp_host` and `smtp_port` (usually 587; STARTTLS is required). Use a provider-issued username and an app password. Follow your provider's SPF, DKIM and DMARC guidance.

## Working locally (optional)

With Python 3.11+ you can check settings and see the agent's prompt without spending anything:

```bash
python -m pip install .
```

```bash
dochunt validate-config
```

```bash
dochunt show-prompt
```

```bash
dochunt preview --synthetic --output-dir out
```

A local paid preview also needs the agent CLI, at the version the workflow pins (`npm install -g @openai/codex@0.163.0` or `npm install -g @anthropic-ai/claude-code@2.1.299`; other versions may reject DocHunt's command), and your API key in the environment:

```bash
dochunt preview --paid --output-dir out
```

## Optional local setup form

If you have Python 3.11+, Git and an authenticated GitHub CLI (`gh auth login`), you can edit settings and secrets in a local browser form instead. From a clean clone of your private repository:

```bash
dochunt setup --repo .
```

The form:

- runs only on `127.0.0.1` until you stop it;
- shows every change before writing;
- commits and pushes only `newsroom.toml`;
- sends secrets to `gh secret set` on standard input.

It always publishes scheduling as disabled before it uploads replacement secrets. Turning the schedule on needs a separate, explicit confirmation.

For a demonstration with fictional settings and no writes, run:

```bash
python -m dochunt.setup_server --mock --no-browser --repo .
```
