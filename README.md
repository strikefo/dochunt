# DocHunt

DocHunt emails reporters a short daily list of **records leads** for their beat: official documents that point to a story, and a ready-to-use first step for getting the records behind it.

Each lead in the email has:

- a headline for the story the records could support;
- **Reporting opportunity**: what the document establishes, who is affected, and what the records could reveal;
- **Source**: a direct link to the document;
- **Get the records**: the responsible office, a contact or submission route checked on an official page that day, and exactly one first step: a finished records request, a call or email script, or a public download with what to extract;
- **Caveat**: the main limitation, when there is one.

A day with nothing worth sending produces a short, honest "no actionable leads" email. Every email says it is AI-generated.

DocHunt grew out of a records-lead briefing a newsroom has used daily for months, and that newsroom now runs on DocHunt itself. It packages that approach so any newsroom can run it for its own beat, agencies and branding.

> **Upgrading from 0.1 (the watch-list version)?** 0.2 replaces the fixed watch-list engine with a research agent, and `newsroom.toml` changes shape. See [updating](docs/updating.md#moving-from-01-watch-lists-to-02-research-agent).

## How it works

Each newsroom runs its own private copy on GitHub, with its own AI and email accounts. There is no shared service. On schedule, GitHub Actions:

1. builds a research prompt from your beat description and the leads you've already been sent;
2. runs an AI research agent ([Codex CLI](https://github.com/openai/codex) with an OpenAI key, or [Claude Code](https://docs.claude.com/en/docs/claude-code) with an Anthropic key) that searches the web, reads the primary documents, verifies contacts, then reviews its own candidates as a skeptical editor;
3. checks the agent's answer in code: real links, one usable first step per lead, a verified route, and no repeats. Broken leads are dropped and the rest are sent;
4. renders your branded email and sends it with Resend or SMTP.

The agent never sees your email credentials, recipients or GitHub token, and it can't send anything. Details: [how it works](docs/how-it-works.md).

## Getting started

1. Click **Use this template → Create a new repository** and make it **private**. Your copy will hold your recipients and sent emails, and the workflow refuses to run in a public repository.
2. Edit `newsroom.toml` with your beat, branding and recipients. Start from an example: [military](examples/military.toml), [education](examples/education.toml), [labor](examples/labor.toml) or [local government](examples/local-government.toml).
3. Add your API keys as GitHub Actions secrets.
4. Run the free fictional `preview`, then one `paid-ai-preview`, and read it. Send yourself a test.
5. Turn on the schedule.

Optional extras:

- **On-time email.** GitHub starts scheduled workflows only a few times a day, so the email can arrive hours late. The included [timer](timer/) is a free Cloudflare Worker that starts DocHunt at your send time. See [scheduling](docs/scheduling.md).
- **Self-service subscriptions.** Instead of a fixed recipient list, DocHunt can read the list from your own small service with an opt-in/opt-out page, and add a "Manage subscription" link to every email. See [configuration](docs/configuration.md#subscription-list-optional).

Full steps: [setup](docs/setup.md). Also see [configuration](docs/configuration.md), [costs and limits](docs/costs-and-limits.md), [scheduling](docs/scheduling.md), [recovery](docs/recovery.md) and [updating](docs/updating.md).

## What it costs

You pay your own providers directly. A run is one agentic research session, plus a repair pass when its answer has fixable problems. The cost depends on your model, reasoning effort and beat, so measure a few paid previews before scheduling. Claude Code stops at the per-run spending cap you set; for Codex, set a spending limit in your OpenAI account. GitHub Actions minutes and your email service may also charge. See [costs and limits](docs/costs-and-limits.md).

## Honest limits

- **AI-written.** The agent can be wrong about a fact, a number, an office or a route. Every email says so. Check facts and filing details before you report or file.
- **Checks are deliberately light.** Code catches broken links, missing contacts, unverified routes and repeats. It doesn't re-verify facts against the source; the agent's own reviewing pass does that. This trade-off is intentional: stricter code gates in earlier versions blocked almost every lead.
- **Quality varies by beat and model.** Tune `brief`, `priorities` and `source_families` after reading your first previews.
- **Testing so far.** DocHunt has run live with the Codex agent on a military beat. The Claude Code agent is covered by offline tests but hasn't yet run live; if you try it, start with a paid preview.
- DocHunt never files requests or contacts agencies.

## Development

```bash
python -m pip install '.[test]'
```

```bash
python -m pytest -q
```

```bash
dochunt preview --synthetic --output-dir out
```

Released under the [MIT License](LICENSE).
