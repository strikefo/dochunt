# Setup

You need:

- a GitHub account that can create **private** repositories and use Actions;
- an API account with billing at OpenAI or Anthropic. A ChatGPT or Claude chat subscription does not include API access;
- an email sending account on a domain you control (Resend, or any SMTP provider with STARTTLS);
- optionally, a [Firecrawl](https://www.firecrawl.dev/) account, for sites that block plain requests.

No local software is required. Everything below can be done on GitHub's website.

## 1. Make your private copy

Choose **Use this template → Create a new repository** (or fork and change visibility), and select **Private**. Enable Actions when GitHub asks. Never make your copy public: it will contain your recipients, your sent emails and your run history.

## 2. Edit `newsroom.toml`

Edit it in GitHub's editor and commit to the default branch. Start from the closest example ([education](../examples/education.toml), [labor](../examples/labor.toml), [military](../examples/military.toml)). Then set:

- **identity and branding**: a newsroom `id` you won't change, the newsletter name, sender name and accent colour;
- **editorial**: a plain-language coverage brief, your geography, exclusions and the maximum number of leads;
- **AI**: `provider` and `model` (see [how it works](how-it-works.md#providers));
- **email**: `provider`, `sender` and `recipients`;
- **schedule**: your IANA `timezone` (such as `America/Chicago`), with `enabled = false` for now;
- **hunt**: your watch list and request pages. This is the most important step; see [configuration](configuration.md#hunt).

## 3. Add secrets

Go to **Settings → Secrets and variables → Actions → New repository secret** and add:

| Secret | When |
|---|---|
| `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` | always (the one for your provider) |
| `RESEND_API_KEY` | `email.provider = "resend"` |
| `SMTP_USERNAME`, `SMTP_PASSWORD` | `email.provider = "smtp"` |
| `FIRECRAWL_API_KEY` | `retrieval.firecrawl_enabled = true` |

Never put a key in `newsroom.toml`, in a workflow input or in an issue.

## 4. Preview, then test, then schedule

Use **Actions → Document Hunt → Run workflow** on the default branch:

1. **`preview`** (free). A fictional newsroom run through the real engine and renderer. Download the `document-hunt-RUN_ID` artifact and open `preview.html`. This only proves that your settings render.
2. **`paid-ai-preview`** (billed). A real run against your watch list. It sends no email and changes no state. Download `paid-ai-preview-RUN_ID` and read:
   - `preview.html`: the email;
   - `engine-result.json`: every lead, approved or held, and why;
   - `analysed-documents.json` and `sources.json`: what was read.

   Expect to adjust your watch list and request pages a few times.
3. **`send-preview-test`**. Sends that exact preview to **one** address from your recipients list. Enter the paid preview's run ID, the recipient, and a new `attempt_id` (letters, digits, `_` and `-`). Check the inbox, spam folder, links and the layout on a phone.
4. **Schedule.** When you are satisfied, set `[schedule] enabled = true`. An hourly check (at minute 17 UTC) starts a run after your `preferred_time` on your `weekdays`, within a four-hour window. GitHub can delay or skip scheduled jobs, and missed days are not backfilled.

Other modes:

- `deliver` runs today's issue now and sends it to everyone.
- `resend` re-sends a chosen paid preview to everyone, under a new attempt ID.

See [scheduling](scheduling.md) and [recovery](recovery.md).

## Email domain

**Resend.** Add and verify your sending domain in Resend and install the DNS records it gives you. Use a sender address at that domain and a key scoped to sending. See [Resend domains](https://resend.com/docs/dashboard/domains/introduction).

**SMTP.** Set `email.provider = "smtp"`, `smtp_host` and `smtp_port` (usually 587; STARTTLS is required). Use a provider-issued username and an app password. Follow your provider's SPF, DKIM and DMARC guidance.

## Firecrawl fallback

Direct fetching always comes first. With `firecrawl_enabled = true`, a public HTML page that blocks plain requests or can't be extracted is fetched through your Firecrawl account. PDFs are never sent to Firecrawl.

- `firecrawl_max_calls` caps attempts per run, including failures.
- `firecrawl_timeout_seconds` sets each attempt's timeout (5 to 60 seconds).
- Each attempt can use credits.

To check the configuration and key presence without any network call:

```bash
python -m dochunt.firecrawl --check newsroom.toml
```

## Optional local setup form

If you have Python 3.11+, Git and an authenticated GitHub CLI (`gh auth login`), you can edit settings and secrets in a local browser form instead. From a clean clone of your private repository:

```bash
python -m pip install .
```

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

The form doesn't edit the `[hunt]` section. It keeps whatever is there, so edit the watch list in `newsroom.toml` directly.
