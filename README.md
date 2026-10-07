# DocHunt

DocHunt watches the places that publish public documents on your beat, such as audit offices, inspectors general, agencies and the Federal Register. When something new appears, it reads the whole document. It then emails you a short briefing with two things:

- what the new document establishes, with every fact quoted from it;
- **the follow-on records request** it points to: which existing records would test the story, who holds them, and a draft request sent to the route the agency itself publishes.

Each newsroom runs its own copy in its own private GitHub repository, with its own AI, email and (optional) page-fetching accounts. There is no shared service, nothing to sign up for, and nobody else sees your beat, your sources or your recipients. Released under the [MIT License](LICENSE).

## What an email looks like

Leads come in two sections:

- **Ready.** The facts are quoted from the new document. Each record the request needs has a route that a fetched agency page shows: an online portal, an email address or a mailing address, plus a draft request you can edit and send.
- **Check the request route first.** The lead is sound, but DocHunt could not confirm from the agency's own pages where the request goes. You confirm the route before filing. These leads never show an unconfirmed address as a link.

A day with nothing new and relevant produces a short "no qualifying leads" note instead.

DocHunt never files requests, never contacts agencies, and treats every lead as a starting point for reporting, not a finding.

## How it decides

For each new document, one model call proposes leads from the whole text, together with your request pages. A second, independent call can only reject. Fixed checks then hold back any lead where:

- a fact, qualification or number is not literally in the source;
- the headline names someone the source doesn't, or runs long or uses unexplained acronyms;
- a request route isn't shown on a fetched page, or names the wrong office or a hotline;
- the request just asks for the document you already have;
- the document is older than the watch window.

Held leads stay in the run's private files so you can see why. Details: [how it works](docs/how-it-works.md).

## Getting started

1. Create a **private** repository from this one. Keep it private: it will hold your recipients and sent emails.
2. Edit `newsroom.toml`: your coverage brief, geography, branding, recipients and, most importantly, the `[hunt]` watch list and request pages for your beat. Start from an example ([education](examples/education.toml), [labor](examples/labor.toml), [military](examples/military.toml)) and a [playbook](playbooks/README.md).
3. Add your API keys as GitHub Actions secrets.
4. Run the free fictional preview, then one paid preview, and read it.
5. Turn on the schedule only when you're happy with what you're seeing.

Full steps: [setup](docs/setup.md). Also see [configuration](docs/configuration.md), [costs and limits](docs/costs-and-limits.md), [scheduling](docs/scheduling.md), [recovery](docs/recovery.md) and [updating](docs/updating.md).

## What it costs

You pay your own providers directly. In our live tests, a watch run that analysed five new documents cost **$0.12–$0.23** in OpenAI calls (GPT-6.1 Sol), plus a few Firecrawl page fetches. GitHub Actions minutes and your email service may also charge. Built-in limits cap calls, tokens, fetches and run time. They are work limits, not a dollar cap, so set spending limits in your provider accounts as well. See [costs and limits](docs/costs-and-limits.md).

## Honest limits

- **Early software.** It has been tested live on a handful of runs, not across many newsrooms or months. See the [evaluation summary](docs/evaluation.md).
- **Acquisition is the hard part.** Many government sites block plain automated requests (HTTP 403). The optional Firecrawl fallback handles HTML pages but not PDFs. A watch list built on publishers' RSS/Atom feeds works best.
- **Conservative about dates.** A document with no clear publication date inside the window is skipped, never reported as new. Some genuinely new documents will be missed.
- **Strict about routes.** The route checks are word-based and sometimes miss valid wording. That is why the "check the route first" section exists.
- **AI-written.** Every email with leads says so. Check facts, dates and the filing procedure before you report or file.

## Development

```bash
python -m pip install '.[test]'
```

```bash
python -m pytest -q
```

Tests are offline: no network, keys, paid calls or email. `dochunt preview --config newsroom.toml --output-dir out --synthetic` renders a fictional email through the real engine at no cost.

Contributions are welcome. Please keep the tests offline, and add a regression test for any gate you change.
