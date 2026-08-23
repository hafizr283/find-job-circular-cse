#!/usr/bin/env python3
"""Mechanical triage pass for the InternBD review queue.

The AI review pass is expensive per job, and most of what it was spending tokens
on needed no judgement at all. In the 2026-08-21 refresh, 119 of 127 drops were
mechanical: the live page said the posting was closed, or the requirements stated
an experience floor at or above the early-career ceiling. Hand-authoring a JSON
verdict with prose for each of those is pure waste.

This module proposes those drops from evidence already in the dataset, prints the
exact phrase each one rests on so a reviewer can scan the lot in one pass, and
writes them straight into data/ai_verdicts.json. The reviewer is then left with
only the jobs that genuinely need reading, which is where the tokens belong.

It never proposes a keep. Deciding a role is worth showing, writing a clean title
and a plain summary, and rating the employer are judgement, and stay manual.

Usage:
    python triage.py                 Show what it would drop, write nothing.
    python triage.py --write         Write data/ai_verdicts.json.
    python triage.py --write --merge Append to an existing verdict file.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
from pathlib import Path

import collector
import ai_review

ROOT = Path(__file__).resolve().parent
JOBS_FILE = ROOT / "data" / "jobs.json"
VERDICTS_FILE = ROOT / "data" / "ai_verdicts.json"

CEILING = collector.EXPERIENCE_YEARS_CEILING

# Pulls the sentence fragment a year figure sits in, so the floor can be checked
# against its own wording rather than trusted blind. The regex has misread a
# company's age ("almost 16 years of experience") as a requirement before.
YEAR_PHRASE = re.compile(
    r"[^.|]{0,60}(?:\d+\s*(?:\+|to|-|–|—)?\s*\d*\s*(?:year|yr)s?)[^.|]{0,50}", re.I
)
# Wording that means the years describe the employer, not the applicant.
COMPANY_AGE = re.compile(
    r"(?:almost|over|nearly|more than|with)\s+\d+\s*(?:\+)?\s*years?\s+of\s+"
    r"(?:proven\s+)?(?:experience|trading|history|operation)",
    re.I,
)


def load_jobs() -> tuple[dict, list]:
    payload = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
    return payload, payload.get("jobs", [])


def evidence(job: dict) -> list:
    text = re.sub(r"\s+", " ", f"{job.get('description') or ''} {job.get('experience_text') or ''}")
    return [m.group(0).strip() for m in YEAR_PHRASE.finditer(text)][:3]


def deadline_passed(job: dict, today: datetime.date):
    raw = (job.get("deadline") or "")[:10]
    if not raw:
        return None
    try:
        due = datetime.date.fromisoformat(raw)
    except ValueError:
        return None
    return due if due < today else None


def triage(jobs: list, today: datetime.date) -> tuple[list, list]:
    """Return (proposals, needs_eyes). Each proposal is a drop verdict."""
    proposals, needs_eyes = [], []

    for job in jobs:
        jid = str(job.get("id"))
        years = job.get("experience_years_min")
        due = deadline_passed(job, today)

        if job.get("posting_status") == "closed":
            proposals.append({
                "id": jid,
                "decision": "drop",
                "reason": "Source page no longer accepts applications.",
                "posting_status": "closed",
                "_why": "live page carried a closed-posting marker",
                "_title": job.get("title", "")[:58],
            })
            continue

        if due is not None:
            proposals.append({
                "id": jid,
                "decision": "drop",
                "reason": f"Stated application deadline of {due.isoformat()} has passed.",
                "posting_status": "closed",
                "_why": f"deadline {due.isoformat()} < today",
                "_title": job.get("title", "")[:58],
            })
            continue

        if isinstance(years, int) and years >= CEILING:
            phrases = evidence(job)
            # A floor that only ever appears as company-age wording is a misparse,
            # so it goes to the reviewer instead of being dropped silently.
            solid = [p for p in phrases if not COMPANY_AGE.search(p)]
            if phrases and not solid:
                needs_eyes.append((jid, job.get("title", "")[:58], years,
                                   "year figures all look like company age", phrases))
                continue
            proposals.append({
                "id": jid,
                "decision": "drop",
                "reason": f"Requirements state a floor of {years} years, above the "
                          f"{CEILING}-year early-career ceiling.",
                "experience_years_min": years,
                "posting_status": "open",
                "_why": (solid or phrases or ["no year phrase found"])[0][:110],
                "_title": job.get("title", "")[:58],
            })
            continue

    return proposals, needs_eyes


def main() -> int:
    parser = argparse.ArgumentParser(description="Mechanical drop triage")
    parser.add_argument("--write", action="store_true", help="write data/ai_verdicts.json")
    parser.add_argument("--merge", action="store_true", help="append to an existing verdict file")
    parser.add_argument("--today", default="", help="override today's date (YYYY-MM-DD)")
    args = parser.parse_args()

    today = (
        datetime.date.fromisoformat(args.today)
        if args.today
        else datetime.datetime.now(datetime.timezone.utc).date()
    )

    payload, jobs = load_jobs()
    proposals, needs_eyes = triage(jobs, today)

    by_why = {}
    for p in proposals:
        key = ("closed" if p.get("posting_status") == "closed" and "deadline" not in p["_why"]
               else "deadline passed" if "deadline" in p["_why"]
               else f"floor >= {CEILING}y")
        by_why.setdefault(key, []).append(p)

    print(f"{len(jobs)} jobs in feed, {len(proposals)} mechanical drops proposed\n")
    for key, group in by_why.items():
        print(f"## {key} ({len(group)})")
        for p in group:
            print(f"  {p['id']}  {p['_title']:<58}  {p['_why'][:88]}")
        print()

    if needs_eyes:
        print(f"## needs a human read ({len(needs_eyes)}) - NOT proposed for drop")
        for jid, title, years, why, phrases in needs_eyes:
            print(f"  {jid}  {title:<58}  exp={years}  {why}")
            for ph in phrases:
                print(f"      {ph[:104]}")
        print()

    kept = len(jobs) - len(proposals)
    print(f"-> {kept} jobs would remain for the judgement pass")

    if not args.write:
        print("\n(dry run; pass --write to emit data/ai_verdicts.json)")
        return 0

    verdicts = [{k: v for k, v in p.items() if not k.startswith("_")} for p in proposals]
    if args.merge and VERDICTS_FILE.exists():
        existing = json.loads(VERDICTS_FILE.read_text(encoding="utf-8")).get("verdicts", [])
        seen = {v["id"] for v in existing}
        verdicts = existing + [v for v in verdicts if v["id"] not in seen]

    VERDICTS_FILE.write_text(
        json.dumps(
            {"review_version": ai_review.REVIEW_VERSION, "verdicts": verdicts},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nwrote {len(verdicts)} verdicts -> {VERDICTS_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
