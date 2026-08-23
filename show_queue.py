#!/usr/bin/env python3
"""Print data/pending_review.json compactly for the review pass.

The raw queue is mostly description text, and a 40-job batch of it is far more
than the reviewer needs to reach a verdict. This trims each entry to the fields
a decision actually turns on and squeezes the description down to the parts that
carry requirements, experience, pay, and any expiry wording.

Usage:
    python show_queue.py [--chars 900]
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
QUEUE_FILE = ROOT / "data" / "pending_review.json"

# Sentences worth keeping: they name a requirement, an experience floor, pay, or
# an expiry. Everything else in a circular is boilerplate about the company.
SIGNAL = re.compile(
    r"experien|year|fresh|graduat|requir|qualifi|skill|salary|stipend|\bbdt\b|\btk\b|"
    r"compensat|intern|no longer|closed|expire|deadline|apply by|cgpa|degree|b\.?sc|"
    r"student|trainee|entry.level|junior|month",
    re.I,
)


def squeeze(text: str, budget: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= budget:
        return text
    parts = re.split(r"(?<=[.!?;:])\s+|(?:\s*[•·\-\*]\s+)", text)
    kept, size = [], 0
    for part in parts:
        part = part.strip()
        if not part or not SIGNAL.search(part):
            continue
        if size + len(part) > budget:
            break
        kept.append(part)
        size += len(part) + 2
    return " | ".join(kept) if kept else text[:budget]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chars", type=int, default=900)
    args = parser.parse_args()

    queue = json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
    print(
        f"# batch {queue['in_this_batch']} of {queue['needing_review']} needing review, "
        f"{queue['remaining_after_batch']} remaining after this batch\n"
    )
    for index, entry in enumerate(queue["pending"], start=1):
        print(f"--- {index}. {entry['id']}")
        print(f"title   : {entry['title']}")
        print(f"company : {entry['company']}  |  {entry['location']}")
        print(
            f"regex   : {entry['regex_job_type']} / {entry['regex_category']} / "
            f"exp={entry['regex_experience_years_min']} / {entry['regex_posting_status']} / {entry['pay_text']}"
        )
        if entry["regex_experience_text"]:
            print(f"exptext : {entry['regex_experience_text'][:160]}")
        print(f"why     : {'; '.join(entry['review_reasons'])}")
        body = squeeze(entry["description_excerpt"], args.chars)
        print(f"desc    : {body if body else '(EMPTY - title only)'}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
