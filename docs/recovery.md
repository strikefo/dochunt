# Delivery and recovery

Commit empty `data/state.json` and `data/history.jsonl` in the newsroom's private repository. Set Git author identity before running. State contains recipient addresses and exact email content: never publish this repository. Credentials come only from environment variables. Git commits include only the two state files; unrelated staged work is excluded. Concurrent Git HEAD or dirty state changes stop delivery. The exclusive local state lock serializes processes on one checkout; workflow concurrency must serialize separate checkouts, which Git push conflict handling must also protect before live use.

Run `dochunt preview --config newsroom.toml --output-dir out` (a paid run) to finalize artifacts. Then `dochunt deliver --issue-dir out --state-dir data --repo .` sends their exact validated final content. A durable pending snapshot and sending marker must commit before the provider call. Retries load the pending sender, provider, recipients and payload, ignoring changed configuration recipients. Accepted state also saves documentary repeat history and the issue completion marker.

Resend retries reuse the same idempotency key and exact request for less than 24 hours from preparation. After that, stop and inspect the provider account manually. SMTP sending/unknown state always stops automatic retries because delivery may have happened. Partial acceptance is unknown and records the known accepted recipient subset; do not resend to the whole list automatically. A crash after provider acceptance but before final persistence retains sending state: Resend can retry inside its window, SMTP requires manual provider inspection.

For an intentional additional send, use `dochunt resend --issue-dir out --attempt-id OPERATOR_SELECTED_ID`. Reuse that ID to recover this same attempt. Do not create a new attempt while another remains unresolved: inspect provider records and decide manually first. Provider accepted is never proof of inbox delivery.

`dochunt preview-test --issue-dir out --recipient CONFIGURED_ADDRESS --attempt-id TEST_ID` sends the existing artifacts to one configured recipient only. It performs no research and does not mark a scheduled issue complete or append repeat history. Synthetic preview artifacts are clearly labeled fictional. Routine tests inject fake HTTPS/SMTP transports and never send live mail.

If atomic writing or Git commit fails, no new provider call occurs. Local changes may remain for operator inspection; restore/commit them deliberately before restarting. If accepted-state writing fails, preserve the sending snapshot and recover above. Do not delete private pending state to make a retry proceed.

State commits use an expected-parent compare-and-swap: an isolated temporary Git index builds a tree from the originally observed parent, `commit-tree` creates the proposed commit, and `update-ref HEAD NEW EXPECTED` atomically rejects a competing revision. The real index and state files are updated only after that succeeds. A rejected competing revision remains intact and no provider call occurs. An interruption after ref update can leave stale worktree files; dirty-state validation stops automatic recovery until the operator reconciles them with committed state.

Unknown preview-test attempts stay in the preview recovery domain; they do not block production delivery or explicit production resends for the same issue. Unknown production attempts still block other production attempts until manual recovery.

`PublishedStateStore` is used for run/deliver/resend/preview-test CLI delivery. Each save publishes its own commit synchronously before returning, including final receipts/history. Fresh-checkout `run` recovers the saved production payload before any research. See [scheduling.md](scheduling.md) for conflict handling, required private-branch permissions, timezone windows and shared application deadline. Local-only StateStore remains a library/test building block and is not the CLI delivery store.

An email-format update changes rendered bodies and their payload fingerprints.
An intact selected preview from the old renderer will fail current render binding;
inspect a fresh preview before a new send instead of rewriting the saved artifact.
Already prepared pending attempts remain frozen: recovery sends their saved bodies,
recipients and idempotency key exactly, Recovery does not regenerate or re-render the issue.
