---
name: internbd-refresh
description: Refresh the InternBD Bangladesh CSE job feed with an AI review pass. Runs the collector when the dataset is stale, then reviews the pending queue - deciding keep or drop, correcting job type and experience floor, confirming the posting is still open, rewriting titles and summaries cleanly, and rating companies against the reputation rubric. Use when the user greets the workspace, says refresh or update the feed, or asks to re-check the jobs.
---

# InternBD refresh

The collector is deterministic and always works alone. This skill is the AI layer
on top of it: the part that reads a circular the way a person would and fixes what
regexes cannot.

Keep the deliberation low and the throughput high. Each job gets a few seconds of
judgement, not an essay. The value is in volume and in catching the three failure
modes below, not in deep analysis of any single listing.

**Spend tokens on keeps, not drops.** Deciding a role is worth showing, writing a
clean title and a plain summary, and rating the employer are judgement. Deciding
that a closed page is closed, or that "5+ years" is above the ceiling, is not.
Step 3 does the second kind mechanically so the expensive pass only sees jobs that
actually need reading. In the 2026-08-21 refresh, 134 of 142 drops were mechanical;
hand-authoring prose verdicts for those was the single largest waste in the run.

## What this pass exists to fix

1. **Closed circulars.** LinkedIn keeps a page live after applications close. The
   collector detects the marker, but a page can also close between scans, and an
   aggregator repost can point at a dead original.
2. **Experience floor.** A circular says "fresh graduates are encouraged" in the
   intro and "at least 3 years" in the requirements. The regex now prefers the
   floor, but ranges, Bangla-English phrasing, and requirements split across
   bullets still slip through.
3. **Aggregator reposts.** Over half the feed comes from `nextjobz` and
   `Bdjobs.com`, which are job boards, not employers. The real company is named
   inside the description, as `Company Name: <employer>` on nextjobz records and
   `<employer> is looking for` on Bdjobs records. Extract it, because company
   rating is meaningless otherwise.

## Before anything else

Set UTF-8 once per session. The circulars carry Bangla text and typographic
dashes, and the Windows console default is cp1252, which will crash any script
that prints them:

```powershell
$env:PYTHONIOENCODING = "utf-8"
```

Run `python -m unittest -q` **now**, before touching anything. If it is already
red, you need to know that was not you.

## Steps

### 1. Decide whether to scan

Read `generated_at` in `data/jobs.json`. Older than 6 hours, or unreadable, means
run the full scan:

```powershell
python collector.py
```

Within 6 hours, skip it. The collector takes several minutes and hits the network
a few hundred times; do not run it for a greeting when the data is already fresh.

### 2. Refill the records the scan could not reach

**Do this before building the queue.** Skipping it wastes an entire review batch
on jobs with no description, and it is the difference between a real review and a
guess.

```powershell
python reenrich.py --delay 1.1
```

`posting_status: "unknown"` with an empty `description` means the detail fetch
**failed**, not that the circular closed. `collector.enrich_one` only sets
`open`/`closed` when the fetch succeeds; on exception the dataclass default
`unknown` survives, and carried-over records keep it across later scans.

Treating `unknown` as closed would have dropped 154 of 283 jobs on 2026-08-21 —
over half the feed — because LinkedIn throttled the concurrent scan. Refetching
sequentially succeeds where the parallel scan fails: 148 of 149 worked at 1.1s
spacing, which turned 141 empty descriptions into 1 and exposed 36 genuinely
closed postings and dozens of real experience floors.

Only ever mark a posting `closed` on evidence from a page you actually fetched.

### 3. Take the mechanical drops off the table

```powershell
python triage.py                 # dry run, prints the evidence for each
python triage.py --write         # writes data/ai_verdicts.json
```

`triage.py` proposes drops for three evidence-backed cases: the live page carried
a closed marker, the stated deadline has already passed, or the requirements state
a floor at or above `collector.EXPERIENCE_YEARS_CEILING`. It prints the exact
phrase each drop rests on, so scan that list once rather than opening each job.

It deliberately withholds anything whose only year figure looks like company age —
`"almost 16 years of experience"` describing the employer has been misread as a
16-year requirement before. Those land in a **needs a human read** section instead.

It never proposes a keep.

Apply it, then continue:

```powershell
python ai_review.py apply
```

### 4. Build the queue

```powershell
python ai_review.py queue --batch 40
```

This writes `data/pending_review.json`, ordered so the most doubtful and highest
scoring jobs come first. It reports how many remain beyond the batch. Never
describe the feed as fully reviewed while that number is above zero.

Read it with `python show_queue.py --chars 600`, which trims each entry to the
fields a decision turns on and squeezes the description to the sentences carrying
requirements, experience, pay or expiry wording. Reading the raw JSON costs
several times as much for the same information.

### 5. Review each pending job

For every entry decide:

- **decision** - `keep` or `drop`. Drop when the role is not CSE, not
  early-career, the posting is closed, or the circular is an unrelated bundle.
- **posting_status** - `open` or `closed`, on the evidence from step 2.
- **experience_years_min** - the real floor from the requirements, as an integer.
  `0` means no experience required. Ignore age limits: "18 years of age" is not
  experience, and neither is a company's trading history.
- **job_type** - `Internship` or `Fresher job`. Nothing else is accepted; a
  category string here is silently ignored by `apply`.
- **category** - one of `valid_categories` in the queue file. Avoid `Other CSE`
  where a real category is defensible: `review_reasons` treats it as unresolved,
  so the job returns to the queue every run.
- **clean_title** - the actual role, without decoration. `Trainee (Paid
  Internship) - Multiple Functions` becomes `IT Trainee`. Strip the mojibake that
  older records carry.
- **clean_summary** - two or three plain sentences: what the role does, who it
  suits, what it pays if stated. No marketing language. Say plainly when a
  circular contradicts itself, for example a "paid internship" whose employment
  type is volunteer.
- **requirements** - a short list of the concrete stated requirements.
- **reason** - one short clause explaining the decision.

### 6. Rate the company when it is unrated

When `review_reasons` includes `company not in the reputation registry`, attach a
`company` object to that verdict. Score it against the five groups documented in
`company_registry.py`:

| Group | Max | Question it answers |
| --- | --- | --- |
| `track_record` | 25 | Will it still exist in two years and pay on time? |
| `engineering` | 25 | Will a fresher learn real engineering here? |
| `early_career` | 20 | Does it actually invest in juniors? |
| `pay_transparency` | 15 | Does it state pay, and is it market rate? |
| `reputation` | 15 | What do employees and the industry say? |

Tiers follow from the total: A at 75 or above, B from 55, C from 35, D below
that. Watch the C/D boundary: a total of 34 reads as "avoid", which is a real
claim about an employer, so do not land there by accident.

Hard red flags force D regardless of score, because they cost the applicant money
or time rather than merely being mediocre: `pay-to-apply`, `training-fee`,
`bond-or-security-deposit`, `mlm-or-commission-only`.

Soft flags cost ranking points and stay visible on the card:
`staffing-agency`, `aggregator-repost`, `unpaid-internship-only`,
`no-pay-disclosed-ever`, `salary-delay-reports`,
`excessive-unpaid-overtime-reports`, `no-verifiable-web-presence`,
`non-cse-bundle`.

Every record needs **provenance**, which `test_company_registry.py` enforces:

- `confidence` - `high`, `medium` or `low`. Use `low` when working from general
  knowledge rather than something you actually read, and prefer middling scores
  over confident guesses.
- `source` - one of `wikipedia`, `clutch`, `model-knowledge`, `feed-observed`.
  Use `feed-observed` when the rating rests on the circular's own text, and
  `model-knowledge` otherwise.

`merge_company` defaults these to `model-knowledge` and `low` if omitted, so a
forgotten field is cautious rather than broken — but state them deliberately.

An unrated company is treated as neutral, so leaving it unrated is always safer
than inventing a tier.

For an aggregator repost, rate the **real employer** named in the description,
and add `aggregator-repost` to the listing company, not to the employer. Note
that the dashboard joins `company_tier` on the listing company, so the employer's
rating will not appear on that card; it still belongs in the registry.

### 7. Write the verdicts and apply

Write `data/ai_verdicts.json`:

```json
{
  "review_version": 1,
  "verdicts": [
    {
      "id": "linkedin-4455239102",
      "decision": "drop",
      "reason": "Source page no longer accepts applications.",
      "posting_status": "closed"
    },
    {
      "id": "linkedin-4451770719",
      "decision": "keep",
      "reason": "Genuine junior support engineering role, no experience floor stated.",
      "job_type": "Fresher job",
      "category": "ITES, Support & Customer Success",
      "experience_years_min": 0,
      "posting_status": "open",
      "clean_title": "Junior Programmer / Support Engineer",
      "clean_summary": "Entry-level support and scripting role in Dhaka. Suits a fresh CSE graduate comfortable with SQL and basic debugging. Pay is not stated.",
      "requirements": ["Bachelor degree in CSE or equivalent", "SQL basics", "Willingness to work on-site in Dhaka"],
      "company": {
        "name": "Example Software Ltd",
        "aliases": ["Example Software"],
        "sector": "service",
        "type": "service",
        "signals": {"track_record": 14, "engineering": 12, "early_career": 11, "pay_transparency": 6, "reputation": 8},
        "flags": [],
        "note": "Small Dhaka services firm; verify pay and working hours before committing.",
        "source": "feed-observed",
        "confidence": "low"
      }
    }
  ]
}
```

Validate coverage before applying — a typo'd id is silently ignored:

```powershell
python -c "import json,io; q={e['id'] for e in json.load(io.open('data/pending_review.json',encoding='utf-8'))['pending']}; v={x['id'] for x in json.load(io.open('data/ai_verdicts.json',encoding='utf-8'))['verdicts']}; print('uncovered',sorted(q-v)); print('extra',sorted(v-q))"
```

Then:

```powershell
python ai_review.py apply
```

`apply` recomputes every score, re-joins company ratings, rewrites
`data/jobs.json` and `data/jobs.js`, merges new companies into
`data/companies.json`, and records dropped ids in `data/ai_rejected_ids.json` so
a later scan does not resurface them. It also drops any job whose
`posting_status` is `closed`, verdict or not.

It does **not** drop jobs on experience alone — only `backfill` and the
collector's scan-time gate do that. That is why step 3 exists.

### 8. Verify and report

```powershell
python -m unittest -q
python -m py_compile collector.py ai_review.py company_registry.py triage.py reenrich.py show_queue.py
node --check app.js
python triage.py          # must now propose zero drops
```

To look at the result, serve the static site and check it renders:

```powershell
python -m http.server 8769 --bind 127.0.0.1
```

Port 8769 rather than 8080, because XAMPP Apache owns 8080 on this machine. The
server must be left **running in the background**; the user cannot see the page if
the command already exited. Confirm the render rather than assuming it:

```powershell
curl -s -o NUL -w "%{http_code}" http://127.0.0.1:8769/data/jobs.js
```

A cached Chromium at
`~/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe` can dump the
built DOM with `--headless --dump-dom --virtual-time-budget=8000`. Check
`id="resultSummary"` and count `class="job-title"`. Both numbers should equal the
dataset total; if the summary reads `N of M` with N below M, the app is hiding
stale rows that `triage.py` should have dropped. Note that `#emptyState` is
always present in the markup with a `hidden` attribute, so finding its text in a
DOM dump does **not** mean the list is empty.

Then report, in a few lines:

- jobs in the feed now, and how many changed
- dropped as closed, and dropped as not early-career
- companies newly rated, and how many companies remain unrated
- how many jobs still sit in the review queue

State the queue remainder explicitly, and distinguish AI-verified jobs from ones
that merely passed the regex confidence test and were never individually read. A
partially reviewed feed described as verified is worse than an unreviewed one,
because it invites trust it has not earned.

## Repeating until the queue drains

One batch is 40 jobs. To work through a backlog, repeat steps 4 to 7. Each
`apply` marks its jobs verified, so the next `queue` returns the next slice
rather than the same one.

## Known gaps, so they are not rediscovered each run

- `app.js` renders `job.title`, and never reads `clean_title`, `clean_summary` or
  `requirements`. The review writes them; the dashboard ignores them. Wiring them
  up is a product decision, so raise it rather than doing it silently.
- `company_tier` joins on the listing company, so aggregator reposts show the job
  board's D tier no matter how good the real employer is. On 2026-08-21 that was
  92 of 156 cards.

## When not to run this

Do not run the collector for a greeting if the dataset is fresh; run the review
pass only. Do not run either if the user greeted you and then immediately asked
for something else - the real request wins.
