#!/usr/bin/env python3
"""Company reputation registry for the InternBD feed.

This module answers one question for every job in the feed: how good is the
company behind it, for a Bangladeshi CSE fresher specifically?

It uses only the standard library, like the collector, and it never fails a
build: an unknown company is neutral, not bad.

Scoring strategy
----------------
A company earns a 0-100 quality score from five weighted groups. Each group is
observable from public pages without logging in, and each one changes whether a
first job is actually worth taking.

    track_record        0-25   Will the company still exist in two years, and
                               pay on time? Age, headcount band, group backing.
    engineering         0-25   Will a fresher learn real engineering? Product
                               work beats body-shopping; public engineering
                               evidence beats claims.
    early_career        0-20   Does the company actually invest in juniors, or
                               does it hire only experienced staff and treat
                               interns as free labour?
    pay_transparency    0-15   Does it state pay, and does it pay Dhaka market
                               rate for freshers? Silence scores zero, never
                               negative, because most BD circulars omit pay.
    reputation          0-15   What do employees and the industry say? Ratings,
                               BASIS membership, complaint volume.

Tiers come from the total, with hard red flags overriding it:

    A  >= 75   Apply first. Real engineering, pays freshers, invests in juniors.
    B  55-74   Solid. Reasonable learning and pay.
    C  35-54   Mixed. Worth it for experience; verify pay and hours yourself.
    D  <  35   Avoid, or verify very carefully before spending effort.

Any hard red flag forces tier D no matter how high the score is, because these
are not quality problems, they are ways a job seeker loses money or time:

    pay-to-apply, training-fee, bond-or-security-deposit, mlm-or-commission-only

Soft flags stay visible on the card and cost ranking points without forcing a
tier: staffing-agency, aggregator-repost, unpaid-internship-only,
no-pay-disclosed-ever, salary-delay-reports, excessive-unpaid-overtime-reports,
no-verifiable-web-presence, non-cse-bundle.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
COMPANIES_FILE = ROOT / "data" / "companies.json"

SCORE_GROUPS = ("track_record", "engineering", "early_career", "pay_transparency", "reputation")
GROUP_CEILINGS = {
    "track_record": 25,
    "engineering": 25,
    "early_career": 20,
    "pay_transparency": 15,
    "reputation": 15,
}

HARD_FLAGS = frozenset(
    {
        "pay-to-apply",
        "training-fee",
        "bond-or-security-deposit",
        "mlm-or-commission-only",
    }
)

SOFT_FLAGS = frozenset(
    {
        "staffing-agency",
        "aggregator-repost",
        "unpaid-internship-only",
        "no-pay-disclosed-ever",
        "salary-delay-reports",
        "excessive-unpaid-overtime-reports",
        "no-verifiable-web-presence",
        "non-cse-bundle",
    }
)

TIER_THRESHOLDS = (("A", 75), ("B", 55), ("C", 35))

SUFFIX_PATTERN = re.compile(
    r"\b(?:limited|ltd|pvt|private|plc|inc|incorporated|corp|corporation|company|co|"
    r"llc|llp|group|holdings|bangladesh|bd|international|intl|technologies|technology|"
    r"tech|solutions|systems|services|software|labs|lab)\b\.?",
    re.I,
)


def normalize_company(name: str) -> str:
    """Collapse a company name to a comparable key."""
    text = re.sub(r"[^\w\s&]+", " ", (name or "").lower())
    text = SUFFIX_PATTERN.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def score_from_signals(signals: dict) -> int:
    """Total a 0-100 score, clamping each group to its ceiling."""
    total = 0
    for group in SCORE_GROUPS:
        value = signals.get(group, 0)
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = 0
        total += max(0, min(value, GROUP_CEILINGS[group]))
    return max(0, min(total, 100))


def tier_from_score(score: int, flags: list | None = None) -> str:
    """Map a score to A/B/C/D, letting hard red flags override it."""
    if flags and HARD_FLAGS.intersection(flags):
        return "D"
    for tier, floor in TIER_THRESHOLDS:
        if score >= floor:
            return tier
    return "D"


class CompanyRegistry:
    """Name to reputation lookup with alias and containment fallbacks."""

    def __init__(self, companies: list | None = None) -> None:
        self.companies = companies or []
        self._exact = {}
        for company in self.companies:
            names = [company.get("name", "")] + list(company.get("aliases", []))
            for candidate in names:
                key = normalize_company(candidate)
                if key:
                    self._exact.setdefault(key, company)
        # Multi-token keys only, longest first, for the containment fallback. A
        # single-token key such as "hired" must never match inside "rehired" or
        # claim an unrelated company that merely starts with the same word.
        self._ordered = sorted(
            ((key, tuple(key.split()), company) for key, company in self._exact.items() if " " in key),
            key=lambda item: -len(item[1]),
        )

    @classmethod
    def load(cls, path: Path | None = None) -> "CompanyRegistry":
        target = path or COMPANIES_FILE
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls([])
        companies = payload.get("companies", []) if isinstance(payload, dict) else payload
        return cls(companies if isinstance(companies, list) else [])

    def __len__(self) -> int:
        return len(self.companies)

    def lookup(self, name: str) -> dict | None:
        key = normalize_company(name)
        if not key:
            return None
        if key in self._exact:
            return self._exact[key]
        # A source company string is often decorated, such as
        # "Impala Intech - Software Development Agency". Match the registry name
        # as a run of whole words so partial words cannot produce a false hit.
        tokens = tuple(key.split())
        for _, key_tokens, company in self._ordered:
            span = len(key_tokens)
            if span > len(tokens):
                continue
            for start in range(len(tokens) - span + 1):
                if tokens[start:start + span] == key_tokens:
                    return company
        return None

    def rating_for(self, name: str) -> dict:
        """Return tier, score, flags, and note for a company name.

        An unlisted company is neutral on purpose. Being absent from a top-250
        list is not evidence of a bad employer, so it must not push a job down.
        """
        company = self.lookup(name)
        if not company:
            return {"tier": "", "score": 0, "flags": [], "note": "Not yet rated"}
        flags = [flag for flag in company.get("flags", []) if isinstance(flag, str)]
        score = company.get("score")
        if not isinstance(score, int):
            score = score_from_signals(company.get("signals", {}))
        tier = company.get("tier") or tier_from_score(score, flags)
        if HARD_FLAGS.intersection(flags):
            tier = "D"
        return {
            "tier": tier,
            "score": score,
            "flags": flags,
            "note": company.get("note", ""),
        }


def annotate(jobs, registry: CompanyRegistry | None = None) -> None:
    """Attach company reputation to every job in place."""
    registry = registry or CompanyRegistry.load()
    for job in jobs:
        rating = registry.rating_for(getattr(job, "company", "") or "")
        job.company_tier = rating["tier"]
        job.company_score = rating["score"]
        job.company_flags = rating["flags"]
        job.company_note = rating["note"]
