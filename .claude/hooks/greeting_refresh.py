#!/usr/bin/env python3
"""UserPromptSubmit hook: turn a bare greeting into an InternBD refresh.

Claude Code pipes the submitted prompt to this script as JSON on stdin. When the
prompt is nothing but a greeting, the script prints a JSON object whose
additionalContext tells Claude to run the refresh procedure. For any other
prompt it prints nothing and exits 0, so normal conversation is untouched.

The staleness decision lives here rather than in the injected text so that
Claude is told what to do, not asked to work out whether to do it.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JOBS_FILE = ROOT / "data" / "jobs.json"
QUEUE_FILE = ROOT / "data" / "pending_review.json"

# A greeting and nothing else. "hi, fix the deadline bug" is a real request and
# must fall through untouched.
GREETING = re.compile(
    r"^\s*(?:hi+|hey+|hello+|yo|salam|assalamu\s*alaikum|good\s+(?:morning|afternoon|evening))"
    r"(?:\s+(?:there|claude|bro|man|again))?"
    r"[\s!.,?~]*$",
    re.I,
)

STALE_AFTER_HOURS = 6


def dataset_age_hours() -> float | None:
    """Hours since the dataset was generated, or None if unreadable."""
    try:
        payload = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
        stamp = payload.get("generated_at")
        if not stamp:
            return None
        generated = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        if generated.tzinfo is None:
            generated = generated.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - generated).total_seconds() / 3600
    except (OSError, ValueError, json.JSONDecodeError, TypeError):
        return None


def pending_count() -> int:
    try:
        payload = json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
        return int(payload.get("needing_review", 0))
    except (OSError, ValueError, json.JSONDecodeError, TypeError):
        return 0


def build_context() -> str:
    age = dataset_age_hours()
    pending = pending_count()

    if age is None:
        age_line = "The dataset timestamp could not be read, so treat it as stale."
        scan = True
    else:
        age_line = f"data/jobs.json was generated {age:.1f} hours ago."
        scan = age > STALE_AFTER_HOURS

    if scan:
        decision = (
            f"That is older than the {STALE_AFTER_HOURS} hour freshness window, so run the "
            "FULL refresh: collector.py first, then the AI review pass."
        )
    else:
        decision = (
            f"That is within the {STALE_AFTER_HOURS} hour freshness window, so SKIP the network "
            "scan and run the AI review pass only."
        )

    return f"""The user greeted you in the InternBD workspace. That is the standing trigger for a
feed refresh, so do the work now rather than replying with a greeting.

Current state: {age_line} {decision}
Jobs awaiting AI review as of the last queue build: {pending} (rebuild to get a live number).

Run the internbd-refresh skill and follow it exactly. The order matters - steps 2
and 3 exist to stop the expensive pass from doing cheap work:

0. Set `$env:PYTHONIOENCODING = "utf-8"`, then run `python -m unittest -q` so a
   pre-existing failure is not mistaken for one you caused.
1. If a full refresh is due, run `python collector.py`.
2. Run `python reenrich.py --delay 1.1` BEFORE building any queue. A
   `posting_status` of "unknown" means the detail fetch failed, NOT that the
   circular closed - do not drop those, refetch them. Skipping this wastes a whole
   review batch on jobs that have no description to review.
3. Run `python triage.py` then `python triage.py --write`, and `python
   ai_review.py apply`. This clears the closed pages, passed deadlines, and stated
   experience floors at or above the ceiling - all evidence-backed, no prose
   needed. Note `apply` does NOT drop on experience by itself.
4. Run `python ai_review.py queue --batch 40`, and read it with
   `python show_queue.py --chars 600` rather than opening the raw JSON.
5. For each remaining job decide keep or drop, correct job_type, category and
   experience_years_min from the description, rewrite the title and summary
   cleanly, and rate any unrated company against the rubric in
   company_registry.py - including its `source` and `confidence`. Keep the
   reasoning brief; this is a high-volume, low-deliberation pass. For an
   aggregator repost, pull the real employer out of the description.
6. Write data/ai_verdicts.json, check every queued id is covered, then run
   `python ai_review.py apply`.
7. Verify: `python -m unittest -q`, `node --check app.js`, and `python triage.py`
   must now propose zero drops. To show the result, leave
   `python -m http.server 8769 --bind 127.0.0.1` running in the BACKGROUND - the
   user sees nothing if that command has already exited.

Report the numbers plainly. Do not claim the feed is fully verified while the
queue still has entries; say how many are left, and distinguish AI-verified jobs
from ones that merely passed the regex confidence test."""


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 0

    prompt = str(payload.get("prompt", ""))
    if not GREETING.match(prompt):
        return 0

    print(
        json.dumps(
            {
                "systemMessage": "InternBD: greeting detected, starting the feed refresh.",
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": build_context(),
                },
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
