This file is sent in full to the LLM on every candidate vacancy — it's the
only place that defines what counts as a fit. Free text, no schema. Rewrite
everything below for yourself.

I'm looking for [grade, e.g. "middle/senior"] roles in [your field, e.g.
"Business Analysis / Project Management / Implementation"].

Roles that fit:
[List job titles you'd actually apply to. Be specific — "Business Analyst"
is broader than "Business Analyst (fintech, requirements-heavy)".]

Experience:
[Years of experience, industries, what you've actually done day to day —
the LLM uses this to judge whether a vacancy's core responsibilities match
your background, not just the job title.]

Work format:
[Remote / hybrid / office, contractor vs full-time, location constraints.]

Target companies (optional):
[Company size, industry, region — only if you have real preferences here.]

Not interested in:
[Roles or domains to reject even if the keyword filter lets them through —
e.g. "pure sales roles", "on-site only", "junior positions".]

## Optional: hard-gap screening

If simple role-title matching lets through too many false positives (e.g.
titles that say "Business Analyst" but the real job is deep backend
engineering or a narrow regulatory domain you've never worked in), add an
explicit instruction block here telling the LLM what to auto-reject even
when the title matches — and just as importantly, what NOT to reject for
(a new industry, unfamiliar tools you could learn, hybrid format, etc.).
Being explicit about the difference between "a learnable gap" and "a
multi-year domain gap" measurably improves precision.
