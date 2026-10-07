# How DocHunt works

## One run

1. **Read the request pages.** These are the FOIA or public-records pages in your config and playbook. They are fetched first, because a follow-on request is useless without its route. Each is capped at 12,000 characters.
2. **Find new documents.** DocHunt reads each page on the watch list:
   - **RSS/Atom feeds** give exact dates. If `keywords` is set, a feed item's title must contain one of them (whole word, any case).
   - **Ordinary listing pages** get a few linked documents opened, and each document's own publication date is read.

   A document counts as new only if its date falls inside the window (14 days by default) and it hasn't been reported before. Dates are read conservatively:
   - labelled dates ("Published", "Released") come first;
   - revision dates, deadlines and comment periods are ignored;
   - a month with no day counts as the first of that month;
   - an undated document is skipped.

   At most `max_new_documents` (5 by default) are analysed per run.
3. **Analyse each new document.** One model call gets your coverage brief, any playbook, the request pages and the **whole** new document. Documents over the character allowance keep their beginning and end, and a visible marker shows the cut. The model proposes leads in a fixed structure:
   - established facts, each with a quote;
   - qualifications, also quoted;
   - what is still unproven;
   - the essential underlying records, each with its own access route (public download or request, custodian, location and the passage that shows it);
   - a draft request for existing records.
4. **Independent review.** A separate call sees the same sources and each proposed lead. It can only **reject**, with reasons. It cannot edit a lead into passing.
5. **Fixed checks.** Application code, not the model, checks every lead:
   - every quote must appear literally in its source. Quotation marks, repeated PDF page headers and number formatting are tolerated, so `43,159` matches `43,159.00`;
   - every number in a stated fact must appear in its source;
   - the lead must rest on the newly published document, and that document must be inside the window;
   - every essential record needs a route:
     - a public download must be described by its own passage;
     - a request route's location must appear in the quote, on the cited page or in its links, and that page must name the custodian;
     - a route that names a different office, or is a hotline, is held;
     - a route may not point back at the document you already have;
   - the draft request must ask for existing records, stay under 1,800 characters and contain no unfilled placeholders;
   - the headline must be at most 14 words, explain its acronyms, and name only people, places and figures that appear in the evidence or your geography.
6. **Email.** Passing leads go into the email. Leads whose only problem is an unconfirmed route go into the second section, "Check the request route first", and their locations are never rendered as links. Everything else is held and listed in the run's private files.
7. **Remember.** After an email is accepted by your provider, the documents it analysed are recorded in `data/state.json`, so they are never analysed or paid for twice. Records older than 180 days are pruned.

Search results and model claims are never evidence. Only text that DocHunt fetched itself is quoted.

## Search mode

`[hunt] mode = "search"` replaces the watch list with one native web-search call that proposes URLs from your brief. The same retriever fetches them, and the same analysis, review and checks follow. It finds topical documents but not necessarily new ones. Watch mode is the recommended default: that is where reporters told us the value is.

## Fetching

All fetching goes through a guarded retriever:

- public addresses only, with every redirect re-checked;
- no cookies or credentials;
- a 1 MB cap per document;
- a separate worker process with a hard timeout.

PDFs are extracted locally. Pages that block plain clients can fall back to [Firecrawl](https://www.firecrawl.dev/) when you enable it with your own key. Firecrawl handles HTML only, never PDFs. Its calls are capped per run and labelled in the run's source records.

## What each run leaves behind

A preview or scheduled run writes:

- `preview.html` and `preview.txt`: the email;
- `issue.json`: the leads as sent;
- `engine-result.json`: every lead, approved or held, with the reasons;
- `sources.json`: the exact text that was read;
- `analysed-documents.json`;
- `raw/`: the model's raw replies.

These let you check any email against its sources after the fact. They stay in your private repository's workflow artifacts for seven days.

## Providers

- **OpenAI**: Responses API with strict JSON schemas. Supported models are `gpt-6.1-sol` (the default, and the one used in our live tests) and `gpt-6-astra`.
- **Anthropic**: Messages API, with the schema included in the instructions. Supported models are `claude-sonnet-5-5`, `claude-opus-5-5` and `claude-haiku-4-5`. The Anthropic path is covered by offline tests but **has not been run live yet**.

`ai.reviewer_model` can name a different model from the same provider for the review call.
