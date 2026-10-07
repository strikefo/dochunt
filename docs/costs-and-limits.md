# Costs and limits

You pay each provider directly from your own account: the AI model, email, GitHub Actions and, optionally, Firecrawl. DocHunt has no fees.

## What runs have cost

These are live watch-mode runs with OpenAI `gpt-6.1-sol` at medium reasoning, priced at $2.50 per million input tokens and $10 per million output tokens (check current pricing):

| Run | New documents analysed | Model calls | Model cost | Time |
|---|---|---|---|---|
| Watch run, plain fetching only | 0 (sites blocked plain requests) | 0 | $0.00 | 19 s |
| Watch run with Firecrawl | 5 | 5 | $0.12 | 95 s |
| Watch run with Firecrawl and topic keywords | 5 | 5 | $0.23 | 4 min |
| Watch run with Firecrawl, two-section email | 5 | 6 | $0.22 | 3.7 min |

Firecrawl scrapes are billed separately, at about one credit per page. A weekday schedule at these rates comes to a few dollars of model calls a month. Your watch list, document lengths and `max_new_documents` change that.

## Built-in ceilings

The `[limits]` section caps every run:

- `research_requests` and `review_requests`: model calls, including retries;
- `searches` and `fetches`;
- `input_tokens` and `output_tokens`;
- `transient_retries`;
- `application_deadline_minutes` (25) inside `workflow_timeout_minutes` (30).

A run that reaches a ceiling stops. If it had not finished analysing any document, it fails and sends nothing, rather than a "no leads" email. If some documents were analysed first, it sends what it found, and the run's `engine-result.json` lists the documents it could not finish.

These are **work limits, not a dollar cap**. A provider reports token use only after a call, so a single accepted call can exceed a ceiling, and a timeout cannot cancel work the provider has already accepted. Set spending limits and alerts in your provider accounts too.

For watch mode with five documents of up to 80,000 characters each, [examples/military.toml](../examples/military.toml) uses `input_tokens = 950000` and `output_tokens = 90000`. That is a worst-case ceiling of about $3.30 per run. Actual use has been far lower.

## Other costs

- **GitHub Actions.** Private repositories use Actions minutes and artifact storage. The hourly schedule check is lightweight, but it runs even while scheduling is disabled.
- **Email.** Resend or your SMTP provider's plan.
- **Firecrawl.** Optional. Each attempt, including failures and timeouts, can use credits. `firecrawl_max_calls` caps attempts per run.
