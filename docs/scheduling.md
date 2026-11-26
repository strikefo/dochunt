# Scheduling

Choose an IANA timezone and leave scheduling disabled until you have reviewed a paid preview and a test send. Each newsroom pays its own AI/email charges and GitHub Actions usage. The hourly minute-17 UTC workflow still consumes lightweight checkout/Python/due-check runner time while disabled; paid research and dependencies are skipped. Private artifact access follows repository access, with seven-day retention; do not install this workflow in a public repository with private reporting or recipients.

The one Document Hunt workflow runs hourly and on manual dispatch, only on the default branch. Dispatch defaults to a fictional offline preview. Explicit deliver uses today's newsroom-local date, bypassing disabled/weekday/time checks for operator recovery, but never completed, unknown SMTP, expired idempotency or older unresolved-issue checks. Automatic eligibility is configured weekdays, from preferred local time inclusive until the earlier of `window_hours` elapsed hours later (default 12, range 1-18) and local midnight: each issue belongs to one local date. There is no automatic backfill. DST repeats use the first occurrence; nonexistent times normalize forward through UTC.

## GitHub's schedule is sparse; use an external timer for on-time email

GitHub delays and skips scheduled workflows, so an hourly schedule can run only a few times a day. A wider window makes a missed weekday much less likely but cannot guarantee one, because GitHub can skip every check in it, and an issue can arrive hours after `preferred_time`.

For on-time delivery, have any external timer start the workflow at your preferred time with `mode=scheduled`. That run gets exactly the scheduled checks (enabled, weekday, window, already sent), so an extra or duplicate trigger is harmless:

```bash
gh workflow run document-hunt.yml -R OWNER/REPO --ref main -f mode=scheduled
```

The simplest option is the included [timer Worker](../timer/): a free Cloudflare Worker that dispatches only `mode=scheduled`, only for this workflow and branch, at your local send hour. Set it up in `timer/wrangler.toml`:

- `GITHUB_REPO`, `WORKFLOW`, `GITHUB_REF`: this repository, `document-hunt.yml`, your default branch.
- `TIME_ZONE` and `SEND_TIME` (both required): the same values as `schedule.timezone` and `schedule.preferred_time`. `SEND_TIME` must be 23:55 or earlier; for a later send time, use a minute-precision external timer instead.

The Worker wakes every 5 minutes and dispatches once a day, at the first wake-up at or after `SEND_TIME` in `TIME_ZONE`, so daylight saving time and any UTC offset need no extra setup. DocHunt's own schedule check still applies your weekdays and send window.

The Worker has no public web address; it runs only from its schedule. Then deploy it with `npx wrangler deploy --config timer/wrangler.toml` and add its token with `npx wrangler secret put GITHUB_TOKEN --config timer/wrangler.toml`. Other options include cron-job.org or any server's crontab. Calling the GitHub API needs a fine-grained token limited to this repository with **Actions: read and write**. That token is not limited to `mode=scheduled`: whoever holds it can also dispatch `deliver` (which sends even while scheduling is disabled) or billed previews, and can download this private repository's workflow artifacts, which contain unpublished leads and rendered emails. Give it only to a timer you trust with those operator rights and with your newsroom's unpublished material, or keep it in a small relay you control (such as your own Cloudflare Worker) that always dispatches this workflow, `main` and `mode=scheduled` and nothing else. Keep the hourly GitHub schedule as a fallback.

`dochunt due-check --config newsroom.toml --state-dir data` prints JSON and writes a boolean due step output when GITHUB_OUTPUT exists. It requires only Python 3.11 standard library and runs before installation or secrets. `dochunt run --branch main --output-dir output` performs due-check, the watch run (analysis, independent review and checks), final render, durable pending/sending publication, then delivery. `--research-only` and `--delivery-only` split the same orchestration so AI secrets and email secrets are present only in their respective stages. Pending recovery skips research and uses the original saved payload, recipients and provider. `--manual` permits current-date operator recovery only. Standalone deliver/resend/preview-test also require remote state publication and accept `--branch`.

The checkout keeps no Git credential, because the research agent runs in the same job. Only the delivery steps receive a push token, through their own step environment: the optional bot GitHub App token when the repository variable `BOT_APP_CLIENT_ID` and secret `BOT_APP_PRIVATE_KEY` are set (use this when a branch ruleset protects the default branch and the App is its bypass actor), otherwise the job's repository-scoped GITHUB_TOKEN with contents write. There is no PAT and no force push. Branch rules must permit that token's targeted fast-forward state and tracker commits; rejection stops before provider access. Configure this deliberately before activation. This code never changes account settings or branch protection. Workflow concurrency serializes Document Hunt runs without cancellation. Other code/state writers must be serialized; if another checkout changes the branch, remote comparison or ordinary fast-forward push stops delivery and requires a fresh checkout. A failed push leaves local commits for inspection; never blindly force-push or retry that checkout.

PublishedStateStore.save saves using an expected-parent compare-and-swap, then publishes each exact pending, sending, unknown and accepted commit (with its tracker update) before returning. A runner lost before provider access leaves remotely durable sending state. Resend recovery within 24 hours reuses its exact snapshot and idempotency key, including after acceptance but before receipt publication. SMTP sending/unknown cannot be retried automatically; provider-record inspection is required. Provider acceptance does not prove inbox delivery. Git/provider operations are not one transaction, and an external writer can still race after the final remote check; deployment must coordinate all writers.

One application deadline of 25 minutes begins before checkout; research and delivery share its remaining time, inside a 30-minute workflow cap. The CLI research Budget additionally enforces configured limits. A timeout may interrupt receipt publication, so follow durable snapshot recovery, not an automatic new attempt. Diagnostic artifacts contain reporting/issue data, not deliberate credential dumps.

Credential-free CI runs on pull requests with contents read, without secrets or retained checkout credentials: tests, example validation, compile, wheel packaging and whitespace checks.

Actions are pinned to full commit SHAs (verified 2026-10-02):

- [checkout v4.2.2](https://github.com/actions/checkout/releases/tag/v4.2.2): [11bd71901bbe5b1630ceea73d27597364c9af683](https://github.com/actions/checkout/commit/11bd71901bbe5b1630ceea73d27597364c9af683)
- [setup-python v5.6.0](https://github.com/actions/setup-python/releases/tag/v5.6.0): [a26af69be951a213d495a4c3e4e4022e16d87065](https://github.com/actions/setup-python/commit/a26af69be951a213d495a4c3e4e4022e16d87065)
- [upload-artifact v4.6.2](https://github.com/actions/upload-artifact/releases/tag/v4.6.2): [ea165f8d65b6e75b540449e92b4886f43607fa02](https://github.com/actions/upload-artifact/commit/ea165f8d65b6e75b540449e92b4886f43607fa02)

Every published state commit includes a fresh random publication-owner UUID in its commit message. This makes competing sending commits distinct even with identical parent, payload, author and Git timestamp. Only the first ordinary fast-forward push can claim the transition; a competing sibling commit is rejected before provider access. The owner UUID does not change the saved payload or provider idempotency key and contains no credential data.
