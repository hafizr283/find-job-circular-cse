# Running InternBD end to end

Every command below assumes PowerShell on Windows from the project root:

```powershell
cd E:\job_project\internship-finder
```

Python 3.10 or newer. The collector and the dashboard need no third-party
packages; only the optional local apply worker installs anything.

The full loop is six stages. Stages 1 to 3 are the daily path. Stage 3.5 is the
AI review pass and needs Claude Code open in this workspace. Stage 4 is optional
and local only. Stage 5 publishes.

```
1. verify -> 2. scan -> 3. serve and review -> 3.5 AI review -> 4. apply locally -> 5. publish
```

The shortcut for stages 2 and 3.5: type `hi` in the Claude Code session. A hook
checks how stale the data is, scans only if it is older than six hours, then runs
the review pass.

---

## 1. Verify the code before a scan

Run these checks. They take a few seconds and catch a broken classifier before it
writes a bad dataset.

```powershell
python -m unittest -v
python -m py_compile collector.py ai_review.py company_registry.py seed_companies.py
python -m py_compile auto_apply.py
node --check app.js
```

Expected: 52 tests pass, no compile output, `node --check` silent. If `node` is
not installed, skip that line; it only lints the frontend.

To check a classifier change against the whole existing dataset without waiting
for a network scan:

```powershell
python ai_review.py backfill
```

That re-derives every deterministic field offline — experience floors, company
tiers, scores — and reports anything it reclassified out of the feed. It is the
fastest regression check available.

## 2. Run today's scan

```powershell
python collector.py
```

What happens:

- 24 taxonomy searches (8 internship, 16 fresher) run against LinkedIn's public
  guest endpoint, paginated at starts `0, 25, 50, 75`, so up to 96 result pages.
  Pagination for one query stops early on the first empty page.
- Every unique posting is enriched from its public detail page, 8 threads at a
  time, to read description, criteria, experience, pay, work mode, and deadline.
- Non-CSE and non-early-career roles are dropped, duplicates are collapsed on
  normalized title plus company plus location, and roles with an explicitly
  passed deadline are removed.
- Roles from the previous dataset survive up to 14 days if the source stops
  returning them; those are flagged `is_fresh: false`.

Runtime is typically 3 to 10 minutes depending on how fast LinkedIn responds.
The command prints nothing until it finishes, then one summary line:

```
Saved 233 CSE roles (41 internships, 192 fresher jobs, 22 newly collected) to E:\job_project\internship-finder\data\jobs.json
```

Files written:

| File | Purpose |
| --- | --- |
| `data/jobs.json` | canonical dataset |
| `data/jobs.js` | same payload as `window.INTERNSHIP_DATA` for the static site |
| `data/seen_ids.json` | every ID ever collected, drives the "newly collected" count |
| `data/notified_ids.json` | IDs Telegram actually accepted, so a failed alert stays retryable |

Exit codes: `0` on success. `1` with `Refresh returned no jobs; preserving the
previous dataset.` means the fetch failed and nothing was overwritten. In that
case check connectivity and rerun; do not commit anything.

### Optional Telegram alerts for a local scan

Alerts fire for every new matching role, including roles with no stated pay.
Without these two variables the scan still completes and just skips alerting.

```powershell
$env:TELEGRAM_BOT_TOKEN="your-bot-token"
$env:TELEGRAM_CHAT_ID="your-chat-id"
python collector.py
```

### Sanity-check the result

```powershell
python -c "import json; d=json.load(open('data/jobs.json',encoding='utf-8')); print(d['generated_at']); print(d['summary']); print(d['source_status'])"
```

Confirm `generated_at` is today, `summary.total` is in the expected range, and
`source_status[0].status` is `ok`. A `status` of `error` means every LinkedIn
query failed.

The summary also reports what was thrown away and why:

- `closed_removed` — circulars whose source page no longer accepts applications.
- `expired_removed` — circulars whose stated deadline has passed.
- `ai_rejected_removed` — roles a previous AI review rejected, filtered from
  `data/ai_rejected_ids.json` so they do not come back every scan.
- `tier_a` / `tier_b` — roles at companies rated A or B.
- `unrated_companies` — distinct companies with no rating yet. A high number is
  normal and harmless; unrated is treated as neutral.
- `ai_verified` — roles that have been through the AI review pass. Zero right
  after a plain scan.

## 3. Serve and review the dashboard

`index.html` loads `data/jobs.js` with a script tag and pulls the Lucide icon
font from a CDN, so serve it over HTTP rather than opening the file directly.

```powershell
python -m http.server 8769
```

Open <http://localhost:8769>. Stop the server with `Ctrl+C`.

Port 8769 rather than the usual 8080 because XAMPP Apache already owns 8080 on
this machine; it answers `/` with a redirect to `/dashboard/`. If you see that
redirect or a 404 for `index.html`, you are talking to Apache, not to
`http.server`. Any free port works.

Review checklist:

- The header timestamp matches the scan you just ran.
- `Internship` and `Fresher job` both appear and both filters work.
- Spot-check two or three cards against their original circular URLs.
- Check the layout at desktop and mobile widths.

Saved jobs, application status, CV filename, and application time live in
browser local storage, so they are per-browser and never leave the machine.

## 3.5 AI review pass

This stage needs Claude Code open in this workspace. Without it, skip to stage 4;
the feed still works, it is just classified by pattern rules alone.

Build the queue of roles the regexes were unsure about:

```powershell
python ai_review.py queue --batch 40
```

That writes `data/pending_review.json`, most doubtful first, and prints how many
remain beyond the batch. Then let Claude work through it — say `hi`, or ask for
the `internbd-refresh` skill directly. Claude writes `data/ai_verdicts.json`, and
the merge is:

```powershell
python ai_review.py apply
```

`apply` rescores everything, re-joins company ratings, rewrites `data/jobs.json`
and `data/jobs.js`, merges newly rated companies into `data/companies.json`, and
records dropped ids in `data/ai_rejected_ids.json`.

Check progress at any time:

```powershell
python ai_review.py status
```

`needing_review` above zero means the feed is partly reviewed. That is fine and
normal — 40 roles per batch — but do not describe the feed as verified until it
reaches zero.

To rebuild the company registry from the tables in `seed_companies.py`:

```powershell
python seed_companies.py
```

That rewrites `data/companies.json` from the tables, and carries through any
company the AI review pass added that the tables do not name, so a rebuild never
deletes a rating you earned. It reports the carried count. Re-run
`python ai_review.py backfill` afterwards so the feed picks up the regenerated
ratings.

## 4. Optional: local apply worker

This stage is deliberately local. It is not part of the scheduled GitHub
Actions run, and it never belongs in a commit.

First-time setup:

```powershell
python -m pip install -r requirements-apply.txt
playwright install chromium
Copy-Item profile.example.json private\profile.json
notepad private\profile.json
```

Then queue roles from the job cards in the dashboard, open the queue button in
the header, and export `internbd-apply-queue.json` into the project root. The
export holds job metadata and public URLs only.

Dry run first, with a visible browser and no Submit click:

```powershell
python auto_apply.py --queue internbd-apply-queue.json --profile private\profile.json --cv "E:\coach\cv\Hafizur_Rahman_SWE_CV_v2.pdf" --dry-run
```

Then the real run by dropping `--dry-run`:

```powershell
python auto_apply.py --queue internbd-apply-queue.json --profile private\profile.json --cv "E:\coach\cv\Hafizur_Rahman_SWE_CV_v2.pdf"
```

Flags: `--log` (default `private\application-log.json`), `--screenshots`
(default `private\application-screenshots`), `--headless`, `--dry-run`.

The worker only submits conventional public forms where it can identify a CV
upload, basic contact fields, and an unambiguous submit button. LinkedIn,
Bdjobs, login walls, CAPTCHA pages, and ambiguous forms are recorded as
`needs_manual` and left for you to finish by hand. Everything it writes lands
under `private\`, which `.gitignore` excludes along with the queue file.

Never commit a CV, a profile, a job-board password, or an access token.

## 5. Publish

### First, a warning about this working copy

This folder is **not its own git repository yet**. The enclosing repo is `E:/`,
whose `origin` is the unrelated `GPU_Programming_Essentials` remote, and
`internship-finder` is untracked inside it. Check before you stage anything:

```powershell
git rev-parse --show-toplevel
git remote -v
```

If that prints `E:/` and a `GPU_Programming_Essentials` URL, do **not** run
`git add` from here. You would commit the whole job tracker, and eventually a
CV path and an application log, into a thesis repository.

Give the project its own repo first. `.gitignore` already excludes `private/`
and `internbd-apply-queue.json`, so a fresh `git init` in this folder is safe:

```powershell
git init
git add .
git commit -m "feat: InternBD CSE early-career job tracker"
git branch -M main
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```

Confirm `git status` shows nothing from `private\` or
`internbd-apply-queue.json` before that first commit.

### Routine refresh commits

Once the project has its own repo, the dataset is generated output that is
committed on purpose, so GitHub Pages serves fresh data:

```powershell
git add data/jobs.json data/jobs.js data/seen_ids.json data/notified_ids.json
git commit -m "chore: refresh CSE job feed"
git push
```

### Automation

For a refresh on this Windows machine every four hours, including after a
reboot, run PowerShell once from the project root:

```powershell
powershell -ExecutionPolicy Bypass -File .\install-refresh-task.ps1
```

This registers `InternBD Job Feed Refresh` in Task Scheduler. It runs the test
check, collector, and review-queue build; output is saved in `logs\`. The task
uses your interactive Windows account and does not store credentials. To remove
it later: `Unregister-ScheduledTask -TaskName 'InternBD Job Feed Refresh'`.

The website uses a separate `InternBD Website` task and starts when your Windows
account logs in after a reboot. Run `powershell -ExecutionPolicy Bypass -File
.\install-site-task.ps1` once to install it. This user-level trigger avoids the
administrator permission required by a system-startup task.

`.github/workflows/daily-refresh.yml` does the same thing automatically on
every push to `main`, on manual dispatch, and on cron at 02:15, 08:15, and
14:15 UTC, which is 08:15, 14:15, and 20:15 Bangladesh time. It runs
`python collector.py`, commits the four data files with `[skip ci]`, and
deploys the whole folder to Pages.

To trigger it by hand instead of scanning locally, run
`Refresh CSE early-career feed` from the repository Actions tab. Note that
GitHub delays scheduled workflows under load and disables them entirely on
repositories with no recent activity.

Pages setup, once per repository: Settings, Pages, source `GitHub Actions`.
Telegram setup, once per repository: add `TELEGRAM_BOT_TOKEN` and
`TELEGRAM_CHAT_ID` as repository secrets.

---

## Manual cross-check sources

The collector only automates LinkedIn. These boards need JavaScript, a login,
or block automation, so they stay as browser links in `SOURCE_DIRECTORY` and in
the dashboard's source checklist. Open them by hand when a scan looks thin:

- [Bdjobs internships](https://bdjobs.com/h/jobs/?JobType=intern)
- [Bdjobs IT & Telecommunication](https://bdjobs.com/h/jobs/?fcatId=8)
- [Careerjet Bangladesh](https://www.careerjet.com.bd/search/jobs?s=software+engineer&l=Bangladesh)
- [Chakri](https://www.chakri.com/)
- [Job.com.bd](https://job.com.bd/)
- [Skill Jobs](https://skill.jobs/)

No public scraper covers every Bangladesh circular. Broad role-family queries,
four pages per query, three runs a day, and Telegram alerts reduce misses; they
do not eliminate them.

## Troubleshooting

**`Refresh returned no jobs; preserving the previous dataset.`**
Every LinkedIn query failed. Check the network, then rerun. Nothing was
overwritten, so there is nothing to revert.

**Scan finishes but `summary.total` collapses.**
Read `source_status[0].message`; it reports how many result pages were checked
and how many listings survived filtering. A low page count means LinkedIn
throttled the run. Rerun later. Roles from the previous dataset are still
carried for 14 days, so one bad scan does not empty the board.

**Dashboard shows stale data.**
`data/jobs.js` is cached by the browser. Hard-reload, or confirm the file's
mtime changed after the scan.

**Dashboard is empty but `data/jobs.json` looks fine.**
`data/jobs.js` did not regenerate, or you opened `index.html` over `file://`, or
another service answered the port. Serve over HTTP as in stage 3.

**A role you expected is missing.**
It was probably rejected as non-CSE or not early-career. Test the classifier
directly instead of guessing:

```powershell
python -c "import collector as c; print(c.is_cse_related('Junior Software Engineer','...description...'))"
```

If a genuine role is being rejected, change the keywords in `collector.py` and
add a case to `test_collector.py` in the same commit.

**Changed the taxonomy or the classifiers.**
Rerun stage 1, then stage 2, then eyeball the dashboard before committing.
`AGENTS.md` holds the product rules those changes must respect.

---

## Reference: a known-good run

Stages 1 to 3 were run end to end on 2026-08-17 on this machine, so these
numbers are a baseline rather than an invention:

- Stage 1: 9 tests passed; `collector.py`, `auto_apply.py`, and `app.js` clean.
- Stage 2: exit `0`, `generated_at 2026-08-17T08:56:03+00:00`, 75 result pages
  checked, 160 of 369 listings kept, 1 expired removed. Total 233 roles: 41
  internships, 192 fresher jobs, 22 newly collected, 155 fresh plus 78 carried
  over from the previous dataset. 7 confirmed paid, 3 likely paid, 12 with a
  known deadline.
- Stage 3: `index.html`, `styles.css`, `app.js`, and `data/jobs.js` all served
  200 on port 8769, and the served payload parsed to 233 jobs with the same
  timestamp.

`notified_ids.json` stayed `[]` because the Telegram variables were unset
locally. That is expected, not a failure.

A later scan returning fewer than roughly 150 roles, or a `deadline_known`
count near zero, is worth a second look.
