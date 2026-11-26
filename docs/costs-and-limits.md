# Costs and limits

You pay each provider directly from your own account: the AI model, email and GitHub Actions. DocHunt has no fees.

## What a run costs

A run is one research session by the agent: it searches the web, reads documents, verifies contacts and reviews its own candidates. When the answer has fixable problems (a missing verification page, a repeat, too many leads), DocHunt asks the agent once to correct them, which costs a second session (skipped when less than about five minutes of the run remain).

Agentic research uses far more tokens than a single model call, and the total varies with your model, `reasoning_effort`, beat and how much is published that day. **Measure before scheduling:** run two or three `paid-ai-preview` runs and read `check-report.json`. Each successful agent attempt records its run time and token usage, and for Claude Code the dollar cost the CLI reports. A failed repair records only its error, and a run that ends without a usable issue writes no `check-report.json`, so check your provider's usage page too. Multiply by your weekdays per month.

For scale, the first live run of the [military example](../examples/military.toml) (Codex, `gpt-6.1-sol`, high reasoning) took about 5 minutes, made 22 web searches, and used about 2.4 million input tokens (2.2 million of them cached) and 15,000 output tokens, producing three leads in one session with no repair. The first live Claude Code preview of the same example (`claude-opus-5-5`, high effort) took about 8 minutes and cost $1.85 by the CLI's own count (about 1.7 million cached input tokens and 22,000 output tokens), producing two leads with no repair. A Claude Code preview of the [local-government example](../examples/local-government.toml) took about 9 minutes and cost $2.75, producing three leads; one of the [education example](../examples/education.toml) took about 7 minutes and cost $2.52, also producing three leads. A Codex preview of the [labor example](../examples/labor.toml) took about 4.5 minutes, made 26 web searches, and used about 3.1 million input tokens (2.9 million cached) and 13,000 output tokens, producing two leads. Price those tokens at your provider's current rates, including its cached-input rate, and check the result against your usage dashboard.

To spend less:

- choose a smaller model or a lower `reasoning_effort`;
- narrow `brief`, `priorities` and `source_families`, so the agent searches less widely;
- lower `max_leads`.

## Built-in limits

- **Claude Code:** `agent.max_budget_usd` (default $5) is the CLI's spending stop for each agent session. It is the CLI's own estimate and can overshoot slightly. A first session that hits it fails the run visibly (`Claude Code stopped at the $... run budget`); a repair session that hits it falls back to the first answer's valid leads. The repair session has its own cap, so one run can spend roughly twice this amount. If a model request fails because the Anthropic account is out of credit, has reached its spend limit, needs organization verification, or rejects the key, that agent session ends with a message naming the cause (`Claude Code could not use the model: ...`). Requests made earlier in the session are still billed, and a failure during the repair session can leave the first attempt's result in place. Fix the cause in the Anthropic Console or the repository secret, then rerun.
- **Codex:** the CLI has no dollar cap, and web searches are billed per call on top of tokens. Set a monthly budget and alerts in your OpenAI account before scheduling; depending on your account settings, a budget may alert rather than stop requests.
- **Time:** each agent session stops after `agent.timeout_minutes` (default 18). The whole run must finish within the workflow's 25-minute application deadline, so the repair session is skipped when there isn't enough time left, and the valid leads from the first answer are sent.

A timeout stops the CLI, but it can't cancel work the provider has already done. Set spending limits and alerts in your provider accounts too.

## Other costs

- **GitHub Actions.** Private repositories use Actions minutes and artifact storage. Each research run installs the agent CLI and runs for several minutes. The hourly schedule check is lightweight, but it runs even while scheduling is disabled.
- **Email.** Resend or your SMTP provider's plan. Resend counts one email per recipient.
- **Timer (optional).** The Cloudflare Worker wakes 288 times a day, within Cloudflare's free plan limits.
