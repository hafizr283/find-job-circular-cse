# InternBD

InternBD is a personal Bangladesh job tracker for CSE internships and real fresher/entry-level jobs. It collects public listings, keeps only CSE-related early-career roles, removes duplicates, closed circulars, and expired deadlines, rates the company behind each role, and serves a searchable static dashboard.

The collector works on its own with no AI. When Claude Code is open in this workspace it adds a review pass on top: reading each doubtful circular, correcting what the regexes got wrong, and rating employers.

## How the two modes work

**Without AI** — `python collector.py` scans, classifies with pattern rules, and writes the dataset. This is what GitHub Actions runs three times a day. It works fine; it is just less precise.

**With AI** — say `hi` in this workspace. A hook checks how old the data is, runs a scan if it is stale, then hands the uncertain roles to Claude, which decides keep or drop, fixes the job type and experience floor, confirms the posting is still open, rewrites messy titles, and rates any company it has not rated yet. There is no API key and no per-job cost.

Nothing breaks if the AI pass never runs. Unreviewed roles keep their pattern-based classification, and unrated companies are treated as neutral.

## What the AI pass fixes

Three problems that pattern matching alone got wrong:

1. **Circulars that stopped accepting applications.** LinkedIn keeps the page live after applications close. The collector now reads the closed marker from the detail page it already fetches, and a closed role is dropped at collection, at build, and again when the page renders.
2. **Experience requirements buried in the text.** A circular saying "fresh graduates are encouraged" up top and "at least 3 years" in the requirements used to be filed as a fresher job. A stated floor of 3 years or more now disqualifies the role no matter how encouraging the intro is.
3. **Job boards posing as employers.** In one scan, 142 of 267 roles came from two aggregator accounts, `nextjobz` and `Bdjobs.com`. Those are boards, not employers, and the real company is named inside the description. They are flagged, ranked down, and can be hidden with one checkbox.

## How companies are rated

`data/companies.json` rates around 340 Bangladeshi companies for one reader: a CSE graduate deciding where to spend an application. Each company earns a 0-100 score from five groups:

| Group | Max | Question it answers |
| --- | --- | --- |
| Track record | 25 | Will it still exist in two years, and pay on time? |
| Engineering | 25 | Will a fresher learn real engineering, or just body-shop? |
| Early career | 20 | Does it actually invest in juniors? |
| Pay transparency | 15 | Does it state pay, and is it market rate? |
| Reputation | 15 | What do employees and the industry say? |

That total becomes a tier:

- **A (75+)** — apply first. Real engineering, pays freshers, invests in juniors.
- **B (55-74)** — solid. Reasonable learning and pay.
- **C (35-54)** — mixed. Worth it for experience; verify pay and hours yourself.
- **D (under 35)** — avoid, or verify very carefully first.

Four red flags force tier D whatever the score, because they cost you money or time rather than just being mediocre: asking applicants to pay, charging a training fee, requiring a bond or security deposit, and commission-only or MLM arrangements. Softer warnings — staffing agency, reposted listing, unpaid-only internship, reports of delayed salary or unpaid overtime — cost ranking points and stay visible on the card.

**An unrated company is treated as neutral, never bad.** Not being on a top-250 list says nothing about an employer, so it never pushes a role down. Tier is a tie-breaker, not a verdict: a strong company cannot bury a fresh, well-paid role at a company nobody has rated yet.

Every rating records where it came from — a fetched Wikipedia list, fetched Clutch profiles, the assistant's own knowledge, or simply "seen in the feed" — plus a confidence level. Ratings marked as unverified assistant knowledge are the first ones re-checked when a job from that company appears.

## Coverage

The collector searches these CSE role families:

- Software development and engineering, including frontend, backend, full stack, mobile, embedded, SDET, game, blockchain, AR/VR, WordPress, Shopify, and low-code roles.
- AI, data, and machine learning, including analyst, BI, data engineering, ML/AI, generative AI, MLOps, analytics engineering, and prompt engineering.
- Cloud, infrastructure, and DevOps, including platform, SRE, network, system administration, database, and cloud roles.
- Cybersecurity and risk, including SOC, penetration testing, IAM, IT audit, and compliance.
- Product, design, and UI/UX, including product design, UX research, interaction design, technical product management, and product ownership.
- ITES, support, and customer success, including helpdesk, service desk, solutions engineering, technical accounts, IT business analysis, and process roles.
- Project management and Agile delivery, when the circular is explicitly early-career.
- Freelance and niche technical roles such as technical writing and green IT.

The dashboard separates `Internship` from `Fresher job`. Senior, lead, principal, director, and other experienced-only roles are rejected. Manager or architect titles are accepted only when the title is explicitly junior, associate, assistant, trainee, graduate, or internship.

## Sources and limitations

- LinkedIn public job results are collected automatically with role-family searches, entry-level filters, and pagination.
- Bdjobs internship and IT categories, Careerjet, Chakri, Job.com.bd, and Skill Jobs are linked as manual cross-check sources.
- The collector runs three times daily through GitHub Actions: approximately 08:15, 14:15, and 20:15 Bangladesh time.
- Telegram alerts include every newly discovered matching role, even when salary is not stated. Successfully notified IDs are tracked separately so a temporary Telegram failure does not silently lose later alerts.

No public scraper can guarantee every Bangladesh job circular. Some boards require JavaScript, login, or block automated requests, and some employers post only on social media or private groups. The app reduces misses through broad role-family queries, four LinkedIn result pages per query, repeated daily runs, Telegram alerts, and a visible source checklist.

## Other behavior

- Opens public LinkedIn detail pages to detect salary, stipend, allowance, unpaid, work-mode, experience, deadline, and closed-posting evidence.
- Removes roles whose explicit parsed deadline has passed, and roles whose source page no longer accepts applications.
- Reads the smallest stated experience requirement and shows it on the card. `No experience needed` means the circular says so; a role demanding three years or more is not treated as a fresher job at all.
- Filters the dashboard by closing window: 3, 7, 14, 30 days, or a custom day count, with a `Deadline soonest` sort and a countdown tag on every role closing within 14 days. Roles with no stated deadline are kept and listed last, and can be hidden with `Stated deadline only`.
- Filters by company quality (`Tier A or B only`), hides job-board reposts and staffing agencies (`Hide reposts and agencies`), and filters to roles stating no experience requirement.
- Keeps a previously collected role for up to 14 days if the source temporarily stops returning it, marking it as not found in the latest scan. A role the source has closed is never brought back.
- Stores saved jobs, application status, CV filename, and application time in browser local storage.
- Keeps the original circular URL for every job.

Pay, experience, and deadline labels are extracted from listing text and are not guarantees. Company tiers are judgement calls, not audited facts. Always verify the original circular before applying. Never pay money to apply for a job.

## Run locally

Python 3.10 or newer is enough; the collector uses only the standard library.

```powershell
cd E:\job_project\internship-finder
python collector.py
python -m http.server 8769
```

Open `http://localhost:8769`.

To rebuild the company registry, or re-derive every field offline after changing a classifier:

```powershell
python seed_companies.py
python ai_review.py backfill
```

`backfill` touches no network, so it is the quick way to check a classifier change against the whole existing dataset.

To see what still needs an AI review:

```powershell
python ai_review.py status
```

For a full end-to-end runbook covering verification, the scan, serving, the
local apply worker, publishing, and troubleshooting, see `run.md`.

Run the tests with:

```powershell
python -m unittest -v
```

## GitHub Pages deployment

1. Put this folder at the root of a GitHub repository.
2. In repository Settings, Pages, select `GitHub Actions` as the source.
3. Push to `main` or run `Refresh CSE early-career feed` from the Actions tab.

Scheduled GitHub Actions can be delayed. A repository with no recent activity may also have scheduled workflows disabled by GitHub.

## Telegram alerts

Create a Telegram bot with BotFather, send one message to the bot, and add these repository secrets:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

For local alerts in PowerShell:

```powershell
$env:TELEGRAM_BOT_TOKEN="your-bot-token"
$env:TELEGRAM_CHAT_ID="your-chat-id"
python collector.py
```

## Local application worker

The dashboard has a private application queue. Queue roles from the job cards, open the queue button in the header, and export `internbd-apply-queue.json`. The export contains job metadata and public URLs only.

The optional local worker uses a visible Playwright browser and keeps your CV, profile, and application log outside the public site:

```powershell
python -m pip install -r requirements-apply.txt
playwright install chromium
Copy-Item profile.example.json private\profile.json
notepad private\profile.json
python auto_apply.py --queue internbd-apply-queue.json --profile private\profile.json --cv "E:\coach\cv\Hafizur_Rahman_SWE_CV_v2.pdf"
```

The worker submits only conventional public forms where it can identify a CV upload, basic contact fields, and a clear submit button. LinkedIn, Bdjobs, login walls, CAPTCHA pages, and ambiguous forms are recorded as `needs_manual` and left available for manual completion. Use `--dry-run` to fill forms without clicking Submit. Results and failure screenshots are written under ignored `private\` paths.

The worker is deliberately local and is not part of the scheduled GitHub Actions collector. Never commit a CV, profile, job-board password, or access token.

## Source policy

The collector uses public, unauthenticated pages and preserves attribution and original links. It does not log in, bypass access controls, apply automatically, or collect applicant data. Sources that cannot be collected reliably remain browser links instead of being scraped with a fragile workaround.
