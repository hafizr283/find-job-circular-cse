# Project Context for Coding Agents

Read this file before changing the project.

## Product goal

InternBD is a static Bangladesh CSE early-career job tracker. It must cover both internships and real fresher/entry-level jobs. Do not narrow it back to paid internships only.

The target user is a CSE student or new graduate in Bangladesh. The feed should contain technical or technology-adjacent CSE roles and exclude unrelated marketing, HR, finance, general sales, medical, textile, civil, and similar circulars.

## Role taxonomy

The accepted families are:

1. Software development and engineering.
2. AI, data, and machine learning.
3. Cloud, infrastructure, networks, databases, and DevOps.
4. Cybersecurity, IT audit, risk, and compliance.
5. Technical product, product design, and UI/UX.
6. ITES, technical support, customer success, solutions, and IT business analysis.
7. IT project management and Agile delivery when explicitly early-career.
8. Freelance and niche technical work such as low-code, WordPress, Shopify, and technical writing.

The detailed role keywords and classifiers live in `collector.py`. Maintain tests in `test_collector.py` when changing them.

## Early-career rules

- `Internship` and `Fresher job` are separate values in `Job.job_type` and separate UI filters.
- Internships are allowed when they are CSE-related.
- Reject senior, lead, principal, staff, head, director, chief, and vice-president titles.
- Manager, architect, and Agile coach titles are not fresher jobs unless the title is explicitly junior, associate, assistant, trainee, graduate, or internship.
- A stated experience floor of 3 years or more disqualifies a role even when the same circular also says fresh graduates may apply. See the experience floor section below.
- LinkedIn entry-level filtering is useful but is not trusted by itself; local classification remains required.

## Collection architecture

- `collector.py` uses only the Python standard library.
- LinkedIn public guest endpoints are the automatic source.
- Search is taxonomy-driven and paginated with starts `0, 25, 50, 75`.
- Each detail page is enriched for description, criteria, category, job type, experience, pay, work mode, deadline, posting status, and score.
- Deduplication uses normalized title, company, and location.
- Explicitly expired deadlines are removed.
- Previous roles can be preserved for 14 days and are marked `is_fresh = false` when absent from the latest scan.
- `data/jobs.json` is the canonical generated dataset; `data/jobs.js` exposes the same payload to the static site.

### Closed postings

A source page can keep serving a circular after applications close. `fetch_job_detail` reads the closed marker from the same detail page it already fetches, so detection costs no extra request. `posting_status` is `open`, `closed`, or `unknown`.

A closed posting is dropped in three places, on purpose:

1. At collection, in `collect_linkedin`.
2. In `main`, before the payload is written.
3. At render time in `app.js`, where `isClosed` feeds `isExpired`.

`preserve_recent` also refuses to resurrect a record whose `posting_status` is `closed`. Keep all four, for the same reason the deadline rule is duplicated: a stale `data/jobs.json` must not be able to show a dead circular.

`unknown` is not `closed`. Records collected before this field existed stay `unknown`, and `ai_review.py backfill` deliberately does not guess.

### Experience floor

`parse_min_experience_years` returns the smallest stated years-of-experience requirement, or `None` when none is stated. A range reports its floor, because that is what an applicant must clear.

`is_early_career` rejects anything at or above `EXPERIENCE_YEARS_CEILING` (3), **regardless of fresher wording**. This is deliberate and is the fix for a real bug: a circular saying "fresh graduates are encouraged" in the intro and "at least 3 years" in the requirements used to be saved as a fresher job. Do not reintroduce an `explicit or not high_experience` style condition that lets boilerplate outvote a stated floor.

The parser ignores age (`18 years of age`) and company-age boilerplate (`45 years of experience in the market`). Keep those exclusions when editing the pattern.

### Response decoding

`decode_response` tries the declared charset, then utf-8, then cp1252, then latin-1. LinkedIn sometimes emits raw windows-1252 punctuation; a plain `utf-8` decode with `errors="replace"` turned dashes and apostrophes into U+FFFD and then fed that corrupted text to the classifiers. Records collected before this fix still contain the corruption; the AI review layer repairs them through `clean_title`.

`DESCRIPTION_LIMIT` is 12000 characters. It was 5000, which cut off requirement blocks at the end of long circulars.

## AI review layer

The project has two modes and must keep working in both.

**No AI available** is the default. `collector.py` classifies with regexes and writes the dataset. Everything works; the feed is just less precise. Never make the collector depend on a reviewer.

**AI available** means Claude Code is in this workspace. There is no API key and no per-job cost; the reviewer is the assistant, working from a queue file.

The flow:

1. `collector.py` writes `data/jobs.json`.
2. `python ai_review.py queue` writes `data/pending_review.json` — the jobs the regexes were not confident about, most doubtful first, capped by `--batch`.
3. The assistant reads the queue and writes `data/ai_verdicts.json`.
4. `python ai_review.py apply` merges verdicts, rescores, re-joins company ratings, rewrites `data/jobs.json` and `data/jobs.js`, and appends dropped ids to `data/ai_rejected_ids.json`.

`data/ai_rejected_ids.json` is load-bearing: `collector.py` filters against it so a later scan does not resurface a role the reviewer already rejected. Do not clear it casually.

`REVIEW_VERSION` in `ai_review.py` invalidates old verdicts. Bump it when the review instructions change materially; every job is then requeued.

`python ai_review.py backfill` recomputes the deterministic fields offline and joins company ratings, with no network access. Use it after changing a classifier or the registry, instead of waiting for a scan.

`ai_review.py` imports `collector`, so `collector.py` must never import `ai_review`.

### The greeting trigger

`.claude/settings.json` registers a `UserPromptSubmit` hook running `.claude/hooks/greeting_refresh.py`. When the submitted prompt is *only* a greeting, the hook injects instructions to run the refresh; anything else passes through untouched. The hook decides staleness itself (6 hour window) so the assistant is told what to do rather than asked to work it out.

`hi, fix the deadline bug` is a real request and must not trigger a refresh. If you widen the greeting pattern, re-test that words like `highlight`, `history`, and `hello world` still fall through.

The procedure itself lives in `.claude/skills/internbd-refresh/SKILL.md`. Keep the hook's summary and the skill in agreement.

## Company reputation registry

`data/companies.json` rates roughly 340 Bangladeshi companies for one specific reader: a CSE fresher deciding where to spend an application.

`company_registry.py` holds the rubric. Five groups, 0-100 total:

| Group | Max | Question |
| --- | --- | --- |
| `track_record` | 25 | Will it still exist in two years and pay on time? |
| `engineering` | 25 | Will a fresher learn real engineering here? |
| `early_career` | 20 | Does it actually invest in juniors? |
| `pay_transparency` | 15 | Does it state pay, and is it market rate? |
| `reputation` | 15 | What do employees and the industry say? |

Tiers: A at 75+, B at 55+, C at 35+, D below. Hard flags (`pay-to-apply`, `training-fee`, `bond-or-security-deposit`, `mlm-or-commission-only`) force D at any score, because they cost the applicant money rather than merely being mediocre. Soft flags cost ranking points and stay visible on the card.

Rules that must hold:

- **An unrated company is neutral, not bad.** Absence from a top-250 list is not evidence of a bad employer, so it must never push a job down. `rating_for` returns tier `""` and score `0`.
- **Tier is a nudge, not a verdict.** `COMPANY_TIER_BOOST` is small on purpose (A is +18) so a strong company cannot bury a fresh, well-paid role at an unrated one. There is a test asserting the A boost stays under 25.
- **Matching is on whole words.** A single-token registry name only matches exactly; multi-token names match as a contiguous run of words. This stops `Hired` matching inside `Rehired Corp` and stops `Square Group` claiming `Square Textiles Division`.
- **Provenance is required.** Every record carries `source` (`wikipedia`, `clutch`, `model-knowledge`, `feed-observed`) and `confidence`. Records marked `model-knowledge` are unverified assistant knowledge and are the first candidates for re-checking. Do not silently promote them.

`seed_companies.py` regenerates the file. Edit the tables there rather than hand-editing JSON, so scoring stays consistent. A rebuild carries through any company already on disk that the tables do not name, so ratings added by a review pass are never destroyed; it reports the carried count. Sector baselines are intentionally modest: a CSE graduate at a bank or a TV channel usually joins an internal IT team, and the tier should say so.

### Aggregators are the biggest data-quality problem

Two accounts, `nextjobz` and `Bdjobs.com`, produced 142 of 267 jobs in one scan. They are job boards, not employers, and the real company is named inside the description. They carry `aggregator-repost`, which drops them to tier D and lets the dashboard hide them.

When reviewing an aggregator posting, rate the **real employer** from the description and leave the flag on the listing company.

## Alerts and scheduling

- `.github/workflows/daily-refresh.yml` runs at 02:15, 08:15, and 14:15 UTC, which is 08:15, 14:15, and 20:15 in Bangladesh.
- CI runs the tests, then the collector, then `ai_review.py queue`. It cannot run the review itself, because there is no AI reviewer in Actions. That is expected: CI keeps the feed collected and the queue ready.
- Telegram must alert on every new matching role, not only roles with stated pay.
- `data/seen_ids.json` tracks collected IDs.
- `data/notified_ids.json` tracks successfully alerted IDs. Do not merge these concepts; failed alerts must remain retryable.

## Coverage boundary

Never claim that the app guarantees every circular. Some Bangladesh boards require JavaScript/login, block automation, or expose listings only through social media. Keep unreliable sources as manual browser links in `SOURCE_DIRECTORY`. Prefer a stable public API or stable server-rendered HTML before adding a new automatic collector.

## Frontend

- The frontend is plain `index.html`, `styles.css`, and `app.js`, served as static files.
- Application tracking and saved jobs stay in browser local storage.
- Preserve the existing compact operational design. Keep internship/fresher type visible on every job card and filterable.
- Deadline handling: a role whose parsed deadline is already past is dropped at render time as well as at collection time, so a stale `data/jobs.json` cannot show closed roles. The deadline window filter (`3/7/14/30`/custom days) only judges roles that stated a deadline; roles without one stay visible and sort last, unless `Stated deadline only` is checked. Keep that rule, since most collected roles state no deadline.
- Posting status: `isClosed` feeds `isExpired`, so a closed circular is dropped at render time too and labelled `No longer accepting`.
- Every card shows a company trust row: tier badge plus any red flags, with the flag text spelled out rather than shown as a raw slug. An unrated company shows `Unrated company` with a tooltip saying unrated is neutral, not bad. Do not restyle a flag to look neutral.
- Company quality filters are `Tier A or B only` and `Hide reposts and agencies`; the experience filter is `No experience required`. Their counts come from `populateFilters`.
- The global payload name remains `window.INTERNSHIP_DATA` for backward compatibility even though it now contains fresher jobs.

## Verification

Run these before handing off changes:

```powershell
python -m unittest -v
python -m py_compile collector.py ai_review.py company_registry.py seed_companies.py
node --check app.js
python ai_review.py backfill
python collector.py
```

`backfill` is the fast check: it re-derives every deterministic field offline, so a classifier regression shows up in seconds instead of after a multi-minute scan. Run the full `collector.py` before claiming a network-facing change works.

Then serve the static site with `python -m http.server 8769` and inspect both desktop and mobile layouts. Port 8080 is already taken by XAMPP Apache on the development machine.

`run.md` is the end-to-end runbook for the same flow.
