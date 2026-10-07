# Evaluation criteria (frozen 2026-10-06, before any engine output)

These criteria are judged by a person reading the **complete** case sources, not the
model packet. Never put this file, case notes or grader conclusions into a model
payload. Change nothing here after outputs exist; add a dated addendum instead.

Report three things separately for every run:

1. **Software result**: did the run complete, which deterministic gates fired, calls,
   input/output tokens, wall time and conservative cost.
2. **Grader assessment**: the scores below, with the grader named (owner, a reporter, or
   "Claude assessment"). An agent's assessment is not a reporter's judgment.
3. **Provider facts**: model, provider, date, and whether the run used saved or live sources.

## Per-lead scores

| Criterion | Pass | Fail |
|---|---|---|
| **Support** | Every factual statement is supported by the complete sources, with dates, scope and material qualifications (including agency responses and later status) kept. | Any material unsupported claim, a dropped qualification that changes the meaning, or a claim about an unread document's contents. One failure makes the lead a **false positive**. |
| **Beyond the seed** | Names specific further records, or a concrete analysis of underlying public records, that the seed does not already answer. | Reading, summarising or re-requesting the seed or its own full report; a question the sources already answer. |
| **Essential-record access** | Every record the reporting test depends on has a route supported by the sources: a public download/portal, or an evidenced custodian plus how to request. | The route reaches some other record (e.g. the audit itself) while the test needs different records; a guessed custodian or destination. |
| **Request practicality** (only if a request is proposed) | Existing records, named categories, bounded dates, a reasonable number of items, addressed to the right custodian. | Asks questions instead of records; agency-wide or open-ended sweeps; requests what is already public. |
| **Presentation** | Headline of at most 14 words a general reader understands; acronyms spelled out or avoided; established facts and hypothesis visibly separated; no process commentary. | Long, acronym-heavy, or overstated headline; hypothesis written as fact. |
| **Usefulness** (0–2) | 2 = a reporter would act on it this week; 1 = worth a look with editing; 0 = would not use. | — |

A lead counts as **useful** only if it passes Support, Beyond the seed and Access, and
scores Usefulness ≥ 1. Presentation and practicality failures are reported but do not
by themselves make a lead a false positive.

## Per-case outcome

| Outcome | Meaning |
|---|---|
| Useful lead | At least one useful lead, no false positive. |
| Correct no-lead | No lead, and the grader, reading the complete sources, finds no opportunity meeting the bar, or finds it blocked by unavailable/inaccessible records the engine correctly identified. |
| Missed opportunity | No lead, but the complete sources support an opportunity meeting the bar. Note whether it was a packet/acquisition loss or a judgment miss. |
| False positive | Any delivered lead failing Support or Access. |
| Run failure | Crash, schema failure, or budget stop. Not an editorial outcome. |

"Held for access" outputs (a promising idea that the engine itself labels as blocked on
access and does not put in the email) are counted as no-lead for the email, and reported
separately as operator notes.

## Thresholds (proposed; owner must confirm before the live run)

- Supplied track (15 cases): zero false positives; useful lead in at least 5 cases;
  missed opportunities caused by packet loss = 0.
- Discovery track: at least half of runs produce one useful lead; zero false positives.
- Cost: median at most $0.75 per supplied case and $2.00 per discovery run at list prices.
- Latency: under 10 minutes per run.

If the supplied track fails on usefulness while support/access pass, the engine is
cautious but weak (prompting or product promise problem). If it fails on false positives,
the gates are insufficient. If the discovery track fails while the supplied track passes,
the problem is acquisition/source discovery, which playbooks are meant to address.

## Comparison

Run each supplied case through the new engine. Where budget allows, run the regression
cases through the current production pipeline with the same saved receipts for one
bounded comparison. Do not iterate prompts against these cases and then rescore them as
evidence of generalization; any prompt change after seeing outputs requires new cases.
