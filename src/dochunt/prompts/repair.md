# Fix your {{newsletter_name}} output

DocHunt checked the JSON you returned for {{date_long}} and found the problems below. Return a corrected JSON object that matches the same schema.

Problems:
{{problems}}

Rules for this correction:
- Fix each problem. You may use web search to verify a route or replace a link, but do not start a new hunt.
- If a lead cannot be fixed, remove it. Do not add new leads.
- Keep every other field as it was unless a fix requires changing it.
- If you remove the lead the subject was about, rewrite the subject. With no leads left, use exactly `No actionable records leads qualified today` and fill `no_lead_reason`.
- Return only the JSON object. Plain text in every field.

Your previous output:

```json
{{previous}}
```

The original instructions follow for reference.

---

{{original}}
