#!/usr/bin/env python3
"""One-off: re-fetch LinkedIn detail pages for records that never got one.

Half the dataset carries an empty description and posting_status "unknown"
because LinkedIn throttled the detail endpoint during an earlier scan. That is a
scraper problem, not evidence the circular closed, so it must not be treated as
closed. This refetches those detail pages slowly and sequentially, which is the
one thing the concurrent scan cannot do, and writes the real description,
criteria, and posting status back into data/jobs.json.

Usage:
    python reenrich.py [--limit N] [--delay 1.5]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import collector

ROOT = Path(__file__).resolve().parent
JOBS_FILE = ROOT / "data" / "jobs.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--delay", type=float, default=1.2)
    args = parser.parse_args()

    payload = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
    jobs = payload["jobs"]

    targets = [
        job
        for job in jobs
        if not job.get("description") or job.get("posting_status") != "open"
    ]
    if args.limit:
        targets = targets[: args.limit]

    fields = collector.Job.__dataclass_fields__
    ok = failed = closed = 0

    for index, raw in enumerate(targets, start=1):
        stub = collector.Job(**{k: raw[k] for k in fields if k in raw})
        try:
            description, criteria, status = collector.fetch_job_detail(stub)
        except Exception as exc:  # noqa: BLE001 - network noise is expected here
            failed += 1
            print(f"[{index}/{len(targets)}] FAIL {raw['id']}: {type(exc).__name__}", flush=True)
            time.sleep(args.delay)
            continue

        stub.description = description
        stub.posting_status = status
        collector.classify_job(stub, description, criteria)

        for key in (
            "description",
            "posting_status",
            "category",
            "job_type",
            "experience_text",
            "experience_years_min",
            "work_mode",
            "pay_status",
            "pay_text",
            "deadline",
            "deadline_text",
            "deadline_status",
            "score",
        ):
            raw[key] = getattr(stub, key)

        ok += 1
        if status == "closed":
            closed += 1
        print(
            f"[{index}/{len(targets)}] {status:6s} {raw['id']} desc={len(description):5d} "
            f"exp={raw.get('experience_years_min')} {raw['title'][:50]}",
            flush=True,
        )
        time.sleep(args.delay)

    JOBS_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nrefetched ok={ok} failed={failed} detected_closed={closed}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
