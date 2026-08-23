#!/usr/bin/env python3
"""AI review layer for the InternBD feed.

The collector is deterministic and works on its own. This module adds an
optional second opinion on top of it, supplied by Claude Code in this workspace.

Flow
----
1. collector.py scans, classifies with regexes, and writes data/jobs.json.
   Anything it is not confident about is queued into data/pending_review.json.
2. Claude Code reads that queue, checks the company and the real requirements,
   and writes verdicts to data/ai_verdicts.json.
3. python ai_review.py apply merges those verdicts back into the dataset,
   persists any newly rated company into data/companies.json, and records
   rejected job ids so a later scan drops them without asking again.

Nothing here is required. With no verdict file the dataset keeps the regex
result, which is the documented no-AI default.

Usage
-----
    python ai_review.py queue [--batch 40]   Build the pending-review queue.
    python ai_review.py apply                Merge data/ai_verdicts.json.
    python ai_review.py status               Show what still needs review.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import collector
from company_registry import CompanyRegistry, score_from_signals, tier_from_score

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
JOBS_FILE = DATA_DIR / "jobs.json"
JOBS_JS_FILE = DATA_DIR / "jobs.js"
QUEUE_FILE = DATA_DIR / "pending_review.json"
VERDICTS_FILE = DATA_DIR / "ai_verdicts.json"
APPLIED_FILE = DATA_DIR / "ai_verdicts.applied.json"
REJECTED_FILE = DATA_DIR / "ai_rejected_ids.json"
COMPANIES_FILE = DATA_DIR / "companies.json"

# Bump when the review instructions change enough that old verdicts are stale.
REVIEW_VERSION = 1

DEFAULT_BATCH = 40

# How much description text the reviewer sees per job. Enough to reach a
# requirements block without flooding the review context.
REVIEW_EXCERPT = 2600

VALID_CATEGORIES = (
    "Software Development & Engineering",
    "AI, Data & Machine Learning",
    "Cloud, Infrastructure & DevOps",
    "Cybersecurity & Risk",
    "Product, Design & UI/UX",
    "ITES, Support & Customer Success",
    "Project Management & Agile",
    "Freelance & Niche Tech",
    "Other CSE",
)

BUNDLE_TITLE_PATTERN = re.compile(
    r"multiple\s+(?:function|position|role|vacanc)|various\s+(?:position|role)|"
    r"several\s+(?:position|role)|\d+\s+(?:position|vacanc)",
    re.I,
)

# Provenance vocabularies shared with seed_companies.py and asserted by
# test_company_registry.py. A rating with no stated basis or certainty is worse
# than no rating, so merge_company falls back to the cautious end of both.
COMPANY_SOURCES = ("wikipedia", "clutch", "model-knowledge", "feed-observed")
COMPANY_CONFIDENCE = ("high", "medium", "low")


def load_json(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_rejected_ids() -> set:
    data = load_json(REJECTED_FILE, [])
    if isinstance(data, dict):
        data = data.get("ids", [])
    return {str(value) for value in data} if isinstance(data, list) else set()


def review_reasons(job: dict, registry: CompanyRegistry) -> list:
    """Why this job still needs a careful look. An empty list means confident."""
    reasons = []
    if job.get("review_status") == "verified" and job.get("review_version") == REVIEW_VERSION:
        return reasons
    if job.get("posting_status") != "open":
        reasons.append("posting status not confirmed open")
    if job.get("experience_years_min") is None and job.get("description"):
        reasons.append("no experience floor parsed from the description")
    if job.get("category") in ("", "Other CSE"):
        reasons.append("category unresolved")
    if not registry.lookup(job.get("company", "")):
        reasons.append("company not in the reputation registry")
    if BUNDLE_TITLE_PATTERN.search(job.get("title", "")):
        reasons.append("title bundles several unrelated roles")
    if not job.get("description"):
        reasons.append("description missing, classification rests on the title alone")
    return reasons


def queue_priority(job: dict, reasons: list) -> tuple:
    """Sort key. The most doubtful and most promising jobs get reviewed first."""
    return (-len(reasons), -int(job.get("score") or 0))


def queue_entry(job: dict, reasons: list) -> dict:
    """One pending-review record: what the reviewer needs and nothing more.

    report.py builds targeted queues through build_queue, so this shape is the
    single definition of what a reviewer is handed.
    """
    return {
        "id": job.get("id", ""),
        "title": job.get("title", ""),
        "company": job.get("company", ""),
        "location": job.get("location", ""),
        "url": job.get("url", ""),
        "posted_at": job.get("posted_at", ""),
        "regex_job_type": job.get("job_type", ""),
        "regex_category": job.get("category", ""),
        "regex_experience_text": job.get("experience_text", ""),
        "regex_experience_years_min": job.get("experience_years_min"),
        "regex_posting_status": job.get("posting_status", ""),
        "pay_text": job.get("pay_text", ""),
        "review_reasons": reasons,
        "description_excerpt": (job.get("description") or "")[:REVIEW_EXCERPT],
    }


def build_queue(
    batch: int = DEFAULT_BATCH,
    only_ids: list | None = None,
    extra_reason: str = "",
) -> dict:
    """Write data/pending_review.json.

    By default this selects the jobs the regexes were unsure about. Passing
    only_ids restricts it to named jobs and skips the confidence test entirely,
    which is how report.py forces a specific job back in front of the reviewer
    after someone reported it as misclassified.
    """
    payload = load_json(JOBS_FILE, {})
    jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
    registry = CompanyRegistry.load()

    wanted = {str(value) for value in only_ids} if only_ids else None
    candidates = []
    for job in jobs:
        if wanted is not None and str(job.get("id")) not in wanted:
            continue
        reasons = review_reasons(job, registry)
        if extra_reason:
            reasons = [extra_reason] + reasons
        if reasons:
            candidates.append((job, reasons))
    candidates.sort(key=lambda pair: queue_priority(pair[0], pair[1]))

    selected = candidates if wanted is not None else candidates[:batch]
    queue = {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "review_version": REVIEW_VERSION,
        "dataset_generated_at": payload.get("generated_at", ""),
        "total_jobs": len(jobs),
        "needing_review": len(candidates),
        "in_this_batch": len(selected),
        "remaining_after_batch": max(0, len(candidates) - len(selected)),
        "valid_categories": list(VALID_CATEGORIES),
        "pending": [queue_entry(job, reasons) for job, reasons in selected],
    }
    write_json(QUEUE_FILE, queue)
    return queue


def coerce_years(value):
    if value is None or value == "":
        return None
    try:
        years = int(value)
    except (TypeError, ValueError):
        return None
    return years if 0 <= years <= 40 else None


def merge_company(entry: dict, companies: list) -> str:
    """Insert or update one company record. Returns added, updated, or skipped."""
    signals = entry.get("signals") or {}
    score = entry.get("score")
    if not isinstance(score, int):
        score = score_from_signals(signals)
    flags = [flag for flag in entry.get("flags", []) if isinstance(flag, str)]
    name = (entry.get("name") or "").strip()
    if not name:
        return "skipped"
    record = {
        "name": name,
        "aliases": [alias for alias in entry.get("aliases", []) if isinstance(alias, str)],
        "domain": entry.get("domain", ""),
        "sector": entry.get("sector", ""),
        "type": entry.get("type", ""),
        "score": score,
        "tier": entry.get("tier") or tier_from_score(score, flags),
        "flags": flags,
        "note": entry.get("note", ""),
        "signals": signals,
        # Provenance travels with the record. Dropping it here used to write
        # ratings the registry could not account for, which is the one thing a
        # reputation store must never do: a tier with no stated basis and no
        # stated certainty reads as fact. Both fields default to the cautious
        # value rather than the flattering one.
        "source": entry["source"] if entry.get("source") in COMPANY_SOURCES else "model-knowledge",
        "confidence": entry["confidence"] if entry.get("confidence") in COMPANY_CONFIDENCE else "low",
        "rated_at": datetime.now(timezone.utc).date().isoformat(),
        "rated_by": "claude-code",
    }
    for index, existing in enumerate(companies):
        if (existing.get("name") or "").strip().lower() == name.lower():
            merged = dict(existing)
            merged.update({k: v for k, v in record.items() if v not in ("", [], {}, None)})
            companies[index] = merged
            return "updated"
    companies.append(record)
    return "added"


def rebuild_summary(payload: dict, jobs: list) -> dict:
    summary = dict(payload.get("summary", {}))
    summary.update(
        {
            "total": len(jobs),
            "internships": sum(job.get("job_type") == "Internship" for job in jobs),
            "fresher_jobs": sum(job.get("job_type") == "Fresher job" for job in jobs),
            "confirmed_paid": sum(job.get("pay_status") == "confirmed" for job in jobs),
            "likely_paid": sum(job.get("pay_status") == "likely" for job in jobs),
            "fresh": sum(bool(job.get("is_fresh")) for job in jobs),
            "sources": len({job.get("source") for job in jobs}),
            "deadline_known": sum(job.get("deadline_status") == "open" for job in jobs),
            "ai_verified": sum(job.get("review_status") == "verified" for job in jobs),
            "tier_a": sum(job.get("company_tier") == "A" for job in jobs),
            "tier_b": sum(job.get("company_tier") == "B" for job in jobs),
            "unrated_companies": len(
                {job.get("company") for job in jobs if not job.get("company_tier")}
            ),
        }
    )
    return summary


def rescore(job: dict) -> int:
    fields = collector.Job.__dataclass_fields__
    return collector.score_job(collector.Job(**{k: job[k] for k in fields if k in job}))


def persist_dataset(
    payload: dict,
    jobs: list,
    registry: CompanyRegistry | None = None,
    extra_summary: dict | None = None,
) -> dict:
    """Re-join company ratings, rescore, rebuild the summary, write both files.

    Every path that mutates the dataset ends here: apply_verdicts, backfill, and
    each mutating report.py command. Scoring depends on the company tier, so the
    join has to happen before the rescore, and both have to happen before the
    summary counts tiers. Keeping that order in one place is the point.

    extra_summary carries caller-specific diagnostics that rebuild_summary does not
    know about, such as backfill's reclassified count.
    """
    if registry is None:
        registry = CompanyRegistry.load()
    for job in jobs:
        rating = registry.rating_for(job.get("company", ""))
        job["company_tier"] = rating["tier"]
        job["company_score"] = rating["score"]
        job["company_flags"] = rating["flags"]
        job["company_note"] = rating["note"]
        job["score"] = rescore(job)

    payload["jobs"] = jobs
    payload["summary"] = rebuild_summary(payload, jobs)
    if extra_summary:
        payload["summary"].update(extra_summary)
    write_json(JOBS_FILE, payload)
    JOBS_JS_FILE.write_text(
        "window.INTERNSHIP_DATA = " + json.dumps(payload, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )
    return payload


def apply_verdicts() -> dict:
    payload = load_json(JOBS_FILE, {})
    if not isinstance(payload, dict) or not payload.get("jobs"):
        raise SystemExit("data/jobs.json is missing or empty; run collector.py first.")
    verdict_payload = load_json(VERDICTS_FILE, {})
    if isinstance(verdict_payload, dict):
        verdicts = verdict_payload.get("verdicts", [])
    else:
        verdicts = verdict_payload
    if not isinstance(verdicts, list) or not verdicts:
        raise SystemExit("data/ai_verdicts.json has no verdicts to apply.")

    by_id = {}
    for verdict in verdicts:
        if isinstance(verdict, dict) and verdict.get("id"):
            by_id[str(verdict["id"])] = verdict

    companies_payload = load_json(COMPANIES_FILE, {"companies": []})
    if isinstance(companies_payload, list):
        companies_payload = {"companies": companies_payload}
    companies = companies_payload.setdefault("companies", [])

    company_changes = {"added": 0, "updated": 0, "skipped": 0}
    for verdict in verdicts:
        entry = verdict.get("company") if isinstance(verdict, dict) else None
        if isinstance(entry, dict) and entry.get("name"):
            company_changes[merge_company(entry, companies)] += 1
    companies_payload["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    companies_payload.setdefault("strategy", "See company_registry.py for the scoring rubric.")
    write_json(COMPANIES_FILE, companies_payload)

    registry = CompanyRegistry.load()
    rejected = load_rejected_ids()
    kept = []
    dropped = 0
    updated = 0

    for job in payload["jobs"]:
        verdict = by_id.get(str(job.get("id")))
        if verdict is None:
            kept.append(job)
            continue
        updated += 1
        if verdict.get("decision") == "drop":
            dropped += 1
            rejected.add(str(job.get("id")))
            continue

        if verdict.get("job_type") in ("Internship", "Fresher job"):
            job["job_type"] = verdict["job_type"]
        if verdict.get("category") in VALID_CATEGORIES:
            job["category"] = verdict["category"]
        years = coerce_years(verdict.get("experience_years_min"))
        if years is not None:
            job["experience_years_min"] = years
        if verdict.get("posting_status") in ("open", "closed"):
            job["posting_status"] = verdict["posting_status"]
        for key in ("clean_title", "clean_summary"):
            value = verdict.get(key)
            if isinstance(value, str) and value.strip():
                job[key] = value.strip()
        if isinstance(verdict.get("requirements"), list):
            job["requirements"] = [
                str(item).strip() for item in verdict["requirements"] if str(item).strip()
            ]
        job["review_status"] = "verified"
        job["review_source"] = "claude-code"
        job["review_version"] = REVIEW_VERSION
        job["review_notes"] = str(verdict.get("reason", ""))[:300]
        kept.append(job)

    # A reviewer verdict of closed removes the job exactly as the collector would.
    closed_now = [job for job in kept if job.get("posting_status") == "closed"]
    for job in closed_now:
        rejected.add(str(job.get("id")))
    kept = [job for job in kept if job.get("posting_status") != "closed"]

    payload["review"] = {
        "mode": "claude-code",
        "review_version": REVIEW_VERSION,
        "reviewed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "verdicts_applied": updated,
        "jobs_dropped": dropped + len(closed_now),
        "verified_total": sum(job.get("review_status") == "verified" for job in kept),
        "companies_added": company_changes["added"],
        "companies_updated": company_changes["updated"],
    }
    persist_dataset(payload, kept, registry)
    write_json(REJECTED_FILE, sorted(rejected))

    # Consume the verdict file so a later apply cannot silently re-apply a stale
    # batch. The applied copy stays on disk as an audit trail.
    try:
        APPLIED_FILE.write_text(
            VERDICTS_FILE.read_text(encoding="utf-8"), encoding="utf-8"
        )
        VERDICTS_FILE.unlink()
    except OSError:
        pass

    return payload["review"]


def backfill() -> dict:
    """Re-derive the deterministic fields on an existing dataset, no network.

    Older records predate posting_status, experience_years_min, and the company
    join. This recomputes everything the collector would compute offline so the
    dashboard is useful straight away, without waiting for a full scan. It never
    invents a posting status: a record the collector never checked stays unknown.
    """
    payload = load_json(JOBS_FILE, {})
    if not isinstance(payload, dict) or not payload.get("jobs"):
        raise SystemExit("data/jobs.json is missing or empty; run collector.py first.")

    registry = CompanyRegistry.load()
    rejected = load_rejected_ids()
    jobs = [job for job in payload["jobs"] if str(job.get("id")) not in rejected]
    removed_rejected = len(payload["jobs"]) - len(jobs)

    reclassified = []
    for job in jobs:
        text = f"{job.get('title', '')} {job.get('description', '')} {job.get('experience_text', '')}"
        years = collector.parse_min_experience_years(text)
        job["experience_years_min"] = years
        job.setdefault("posting_status", "unknown")
        job.setdefault("review_status", "unreviewed")
        job.setdefault("review_source", "regex")
        job.setdefault("review_notes", "")
        job.setdefault("clean_title", "")
        job.setdefault("clean_summary", "")
        job.setdefault("requirements", [])
        # Records collected before report.py existed were all scan-collected.
        job.setdefault("origin", "scan")

        if years is not None and years >= collector.EXPERIENCE_YEARS_CEILING:
            reclassified.append((job.get("id"), job.get("title", "")[:60], years))

    # A stated floor at or above the ceiling is not an early-career role.
    drop_ids = {job_id for job_id, _, _ in reclassified}
    jobs = [job for job in jobs if job.get("id") not in drop_ids]

    persist_dataset(
        payload,
        jobs,
        registry,
        extra_summary={
            "experience_reclassified": len(reclassified),
            "companies_rated": len(registry),
        },
    )
    return {
        "jobs": len(jobs),
        "removed_previously_rejected": removed_rejected,
        "reclassified_out": reclassified,
        "companies_rated": len(registry),
        "unrated_companies": payload["summary"]["unrated_companies"],
    }


def show_status() -> dict:
    payload = load_json(JOBS_FILE, {})
    jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
    registry = CompanyRegistry.load()
    needing = [job for job in jobs if review_reasons(job, registry)]
    status = {
        "dataset_generated_at": payload.get("generated_at", ""),
        "total_jobs": len(jobs),
        "verified": sum(job.get("review_status") == "verified" for job in jobs),
        "needing_review": len(needing),
        "companies_rated": len(registry),
        "rejected_ids": len(load_rejected_ids()),
    }
    print(json.dumps(status, indent=2))
    return status


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="InternBD AI review layer")
    parser.add_argument("command", choices=("queue", "apply", "status", "backfill"))
    parser.add_argument("--batch", type=int, default=DEFAULT_BATCH)
    args = parser.parse_args(argv)

    if args.command == "backfill":
        result = backfill()
        print(
            f"Backfilled {result['jobs']} jobs. "
            f"Reclassified out of fresher: {len(result['reclassified_out'])}. "
            f"Removed previously rejected: {result['removed_previously_rejected']}. "
            f"Companies rated: {result['companies_rated']}, still unrated: {result['unrated_companies']}."
        )
        for job_id, title, years in result["reclassified_out"]:
            print(f"  dropped {job_id}: {title} (stated floor {years} years)")
        return 0

    if args.command == "queue":
        queue = build_queue(args.batch)
        batch_size = queue["in_this_batch"]
        needing = queue["needing_review"]
        remaining = queue["remaining_after_batch"]
        print(
            f"Queued {batch_size} of {needing} jobs needing review "
            f"({remaining} remaining) -> {QUEUE_FILE}"
        )
        return 0
    if args.command == "apply":
        review = apply_verdicts()
        applied = review["verdicts_applied"]
        removed = review["jobs_dropped"]
        verified = review["verified_total"]
        added = review["companies_added"]
        touched = review["companies_updated"]
        print(
            f"Applied {applied} verdicts, dropped {removed} jobs, {verified} verified, "
            f"companies added {added}, companies updated {touched}"
        )
        return 0
    show_status()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
