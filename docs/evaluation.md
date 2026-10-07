# Evaluation so far

This is a small amount of evidence, reported plainly. The grading was done by the developers with an AI assistant, not by a panel of reporters.

## Method

The cases, criteria and thresholds in [eval/](../eval/) were frozen **before** the current engine existed. That way, cases could not be picked to flatter it. Supplied-source cases replay saved public documents offline. Live runs used OpenAI `gpt-6.1-sol`.

## Supplied-source runs (older documents)

| Set | Cases | Leads emailed | Notes |
|---|---|---|---|
| First live set | 6 | 4 leads, in 3 cases | Every emailed fact was checked against the full source, and no unsupported claim or wrong route was found. Two leads were rated strong: a student-loan servicer oversight lapse, and a police-overtime vs. event-cost-recovery comparison built only from public data. Two were narrower. |

Feedback from the reporter testing it: the leads were reasonable, but **built on documents that were months or years old.** The value of a document hunter is spotting new documents and suggesting the next request. That feedback produced watch mode.

## Watch-mode runs (new documents, military beat)

| Run | Result |
|---|---|
| W1 | No readable new documents. Federal sites returned HTTP 403 to plain requests. That finding led to the Firecrawl fallback. |
| W2 | Five genuinely new documents found. No lead, for sound reasons: the documents were off-beat, and no request route was available. |
| W3 | A topic filter skipped 20 off-beat feed items. The Navy FOIA route was found. Good leads were held by over-strict route wording checks. |
| W4 | **One ready lead emailed.** It came from a GAO report on VA harassment, published the day before, and carried a follow-on request naming record categories taken from VA's own FOIA page. |

## What this does and doesn't show

- The checks work as designed. Hundreds of offline tests cover each gate, including real false positives from earlier versions. No live email has contained a fabricated quote.
- Acquisition, not analysis, is the main constraint. Blocked sites, PDFs that Firecrawl can't handle, and undated listing pages limit what a run can see.
- The route checks are word-based and still miss some valid wording. The "check the route first" section limits the cost of those misses.
- **Not yet shown:** consistent value over weeks, on other beats, with Anthropic models, or as judged by working reporters. If you run DocHunt, your findings are the next evidence. Please share them in an issue (never share private leads or recipients).
