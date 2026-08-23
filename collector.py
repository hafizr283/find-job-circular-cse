#!/usr/bin/env python3
"""Collect Bangladesh CSE internships and fresher jobs from public pages.

The collector intentionally uses only public, unauthenticated endpoints. Every
job keeps its original source URL; this project never submits applications or
stores job-board credentials.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import company_registry


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
OUTPUT_FILE = DATA_DIR / "jobs.json"
JOBS_JS_FILE = DATA_DIR / "jobs.js"
SEEN_FILE = DATA_DIR / "seen_ids.json"
NOTIFIED_FILE = DATA_DIR / "notified_ids.json"
REJECTED_FILE = DATA_DIR / "ai_rejected_ids.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

SEARCHES = (
    # LinkedIn experience filter 1 is internship and 2 is entry level.
    ("software developer QA intern", "Internship", "1"),
    ("mobile embedded IoT intern", "Internship", "1"),
    ("data AI machine learning intern", "Internship", "1"),
    ("cloud DevOps network intern", "Internship", "1"),
    ("cybersecurity IT audit intern", "Internship", "1"),
    ("UI UX product intern", "Internship", "1"),
    ("technical support business analyst intern", "Internship", "1"),
    ("IT project management intern", "Internship", "1"),
    ('("software engineer" OR "software developer" OR "frontend developer" OR "backend developer" OR "full stack developer")', "Fresher job", "2"),
    ('("mobile developer" OR "android developer" OR "flutter developer" OR "react native developer")', "Fresher job", "2"),
    ('("embedded engineer" OR "firmware engineer" OR "game developer" OR "blockchain developer" OR "AR VR developer")', "Fresher job", "2"),
    ('("SDET" OR "QA engineer" OR "SQA engineer" OR "test automation engineer")', "Fresher job", "2"),
    ('("data analyst" OR "data scientist" OR "data engineer" OR "BI developer")', "Fresher job", "2"),
    ('("machine learning engineer" OR "AI engineer" OR "MLOps engineer" OR "analytics engineer" OR "prompt engineer")', "Fresher job", "2"),
    ('("DevOps engineer" OR "platform engineer" OR "site reliability engineer" OR "cloud engineer")', "Fresher job", "2"),
    ('("system administrator" OR "database administrator" OR "network engineer")', "Fresher job", "2"),
    ('("cybersecurity analyst" OR "SOC analyst" OR "penetration tester" OR "IAM specialist" OR "IT auditor")', "Fresher job", "2"),
    ('("UI UX designer" OR "product designer" OR "UX researcher" OR "interaction designer")', "Fresher job", "2"),
    ('("technical product manager" OR "product owner" OR "IT business analyst")', "Fresher job", "2"),
    ('("technical support engineer" OR "service desk analyst" OR "IT helpdesk" OR "solutions engineer")', "Fresher job", "2"),
    ('("customer success" OR "technical account manager" OR "pre sales engineer" OR "process associate")', "Fresher job", "2"),
    ('("scrum master" OR "IT project manager" OR "delivery manager" OR "release train engineer")', "Fresher job", "2"),
    ('("low code developer" OR "WordPress developer" OR "Shopify developer" OR "technical writer")', "Fresher job", "2"),
    ("MTO IT graduate trainee technology", "Fresher job", "2"),
)
SEARCH_STARTS = (0, 25, 50, 75)

# Requirement blocks often sit at the end of a long circular, so keep enough
# text for the experience parser and the AI reviewer to see them.
DESCRIPTION_LIMIT = 12000

# Detail-page concurrency. Eight workers made LinkedIn time out on most
# requests; four is slower per scan but actually returns descriptions.
ENRICH_WORKERS = 4

SOURCE_DIRECTORY = (
    {
        "name": "LinkedIn Jobs",
        "url": "https://bd.linkedin.com/jobs/search?keywords=software%20engineer&location=Bangladesh&f_E=1%2C2",
        "kind": "automatic",
        "note": "CSE internship and entry-level searches are collected automatically.",
    },
    {
        "name": "Bdjobs internships",
        "url": "https://bdjobs.com/h/jobs/?JobType=intern",
        "kind": "browser",
        "note": "Cross-check the live internship feed.",
    },
    {
        "name": "Bdjobs IT & Telecommunication",
        "url": "https://bdjobs.com/h/jobs/?fcatId=8",
        "kind": "browser",
        "note": "Cross-check the full IT category for fresher circulars.",
    },
    {
        "name": "Careerjet Bangladesh",
        "url": "https://www.careerjet.com.bd/search/jobs?s=software+engineer&l=Bangladesh",
        "kind": "browser",
        "note": "Searches software roles across Bangladesh.",
    },
    {
        "name": "Chakri",
        "url": "https://www.chakri.com/",
        "kind": "browser",
        "note": "Search for intern or internship roles.",
    },
    {
        "name": "Job.com.bd",
        "url": "https://job.com.bd/",
        "kind": "browser",
        "note": "Bangladesh job portal; availability varies.",
    },
    {
        "name": "Skill Jobs",
        "url": "https://skill.jobs/",
        "kind": "browser",
        "note": "Cross-check technology and graduate openings.",
    },
)


@dataclass
class Job:
    id: str
    title: str
    company: str
    location: str
    url: str
    source: str
    posted_at: str
    collected_at: str
    logo: str = ""
    description: str = ""
    category: str = "Other CSE"
    job_type: str = "Internship"
    experience_text: str = ""
    work_mode: str = "On-site"
    pay_status: str = "unknown"
    pay_text: str = ""
    deadline: str = ""
    deadline_text: str = ""
    deadline_status: str = "unknown"
    score: int = 0
    is_fresh: bool = True
    # open | closed | unknown. "closed" means the source page said the posting
    # stopped accepting applications, which is independent of any deadline date.
    posting_status: str = "unknown"
    # Smallest stated years-of-experience requirement, or None when unstated.
    experience_years_min: int | None = None
    # Company reputation, joined from data/companies.json at build time.
    company_tier: str = ""
    company_score: int = 0
    company_flags: list[str] = field(default_factory=list)
    company_note: str = ""
    # AI review layer. Without a reviewer these keep their defaults and the
    # feed behaves exactly like the regex-only pipeline.
    review_status: str = "unreviewed"
    review_source: str = "regex"
    review_notes: str = ""
    clean_title: str = ""
    clean_summary: str = ""
    requirements: list[str] = field(default_factory=list)


def decode_response(raw: bytes, declared_charset: str | None = None) -> str:
    """Decode a response body without turning dashes and quotes into U+FFFD.

    LinkedIn guest endpoints usually serve UTF-8 but sometimes emit raw
    windows-1252 bytes for punctuation. A plain utf-8 decode with
    errors="replace" silently corrupts those, and the corrupted text then feeds
    the classifiers, so try the declared charset first and fall back in order.
    """
    candidates = []
    if declared_charset:
        candidates.append(declared_charset)
    candidates.extend(("utf-8", "cp1252", "latin-1"))
    for charset in candidates:
        try:
            return raw.decode(charset)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


class RateLimitError(RuntimeError):
    """The source answered 403/429 and stayed throttled through every retry."""


# LinkedIn throttles with 403 and 429. Retrying those on the same ~1.5s linear
# schedule as a timeout just burns attempts against an active throttle; they get
# exponential backoff instead. Timeouts and connection errors keep the old
# linear schedule.
RATE_LIMIT_STATUSES = frozenset((403, 429))
RATE_LIMIT_BACKOFF_SECONDS = 5  # Delays grow x3 per retry: 5s, 15s, 45s.


def fetch(url: str, timeout: int = 25, retries: int = 2) -> str:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    }
    error: Exception | None = None
    attempt = 0
    while attempt <= retries:
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return decode_response(response.read(), response.headers.get_content_charset())
        except urllib.error.HTTPError as exc:
            # HTTPError subclasses URLError, so it must be caught first.
            if exc.code not in RATE_LIMIT_STATUSES:
                raise
            error = exc
            if attempt < retries:
                time.sleep(RATE_LIMIT_BACKOFF_SECONDS * 3**attempt)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            error = exc
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
        attempt += 1
    if isinstance(error, urllib.error.HTTPError):
        raise RateLimitError(f"{url} answered {error.code} after {retries} backoff retries")
    raise RuntimeError(f"Could not fetch {url}: {error}")


def clean_markup(value: str) -> str:
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def first_match(pattern: str, value: str, flags: int = re.I | re.S) -> str:
    match = re.search(pattern, value, flags)
    return clean_markup(match.group(1)) if match else ""


def normalize_url(value: str) -> str:
    value = html.unescape(value.strip())
    parsed = urllib.parse.urlsplit(value)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


TECH_ROLE_PATTERN = re.compile(
    r"software\s+(?:intern|trainee|engineer|developer|development|quality|test|support|architect)|"
    r"web\s+(?:developer|engineer)|front[ -]?end|back[ -]?end|full[ -]?stack|"
    r"programmer|application\s+(?:developer|engineer)|mobile\s+(?:developer|engineer)|"
    r"android|ios\s+developer|react\s+native|flutter|laravel|django|\.net\s+(?:developer|engineer)|"
    r"java\s+(?:developer|engineer)|python\s+(?:developer|engineer)|"
    r"embedded\s+(?:system|engineer|developer)|firmware|game\s+(?:developer|programmer)|\bunity\b|unreal|blockchain|web3|smart contract|"
    r"spatial computing|augmented reality|virtual reality|\bar\s*/?\s*vr\b|"
    r"\bqa\b|\bsqa\b|quality\s+assurance|software\s+test|test\s+(?:engineer|automation)|"
    r"\bsdet\b|"
    r"data\s+(?:analyst|scientist|engineer)|business\s+intelligence|machine\s+learning|"
    r"artificial\s+intelligence|\bai\s+(?:engineer|developer)|\bml\s+(?:engineer|developer)|"
    r"generative\s+ai|large language model|\bllm\b|\brag\b|mlops|analytics\s+engineer|prompt\s+engineer|"
    r"ai ethics|ai compliance|"
    r"cyber\s*security|security\s+(?:analyst|engineer)|soc\s+analyst|penetration\s+test|"
    r"ethical hacker|identity and access|\biam\b|it\s+audit|compliance\s+analyst|"
    r"network\s+(?:engineer|administrator)|system(?:s)?\s+(?:engineer|administrator)|"
    r"\bit\s+(?:support|specialist|officer|executive|engineer)|technical\s+support|"
    r"service\s+desk|helpdesk|customer\s+success|technical\s+account|solutions\s+engineer|pre[ -]?sales\s+engineer|"
    r"it\s+business\s+analyst|process\s+associate|\bbpo\b|\bkpo\b|"
    r"devops|platform\s+engineer|cloud\s+(?:architect|engineer|support|network)|site\s+reliability|"
    r"database\s+administrator|\bdba\b|"
    r"ui\s*/?\s*ux|user\s+experience|ux\s+research|interaction\s+designer|product\s+designer|"
    r"technical\s+product\s+manager|product\s+owner|salesforce\s+(?:developer|technical|consultant)|"
    r"scrum\s+master|agile\s+coach|it\s+project\s+manager|delivery\s+manager|release\s+train\s+engineer|"
    r"low[ -]?code|no[ -]?code|wordpress|shopify|technical\s+writer|green\s+it|"
    r"robot|internet\s+of\s+things|\biot\b|embedded\s+(?:system|engineer)|computer\s+vision",
    re.I,
)
GENERIC_TECH_ROLE_PATTERN = re.compile(
    r"software\s+(?:intern|trainee|engineer|developer|development|quality|test|support)|"
    r"web\s+(?:developer|engineer)|front[ -]?end|back[ -]?end|full[ -]?stack|mobile\s+(?:developer|engineer)|"
    r"android|ios\s+developer|react\s+native|flutter|embedded\s+(?:system|engineer|developer)|firmware|"
    r"\bqa\b[^.;\n]{0,80}(?:software|web|mobile|api)|(?:software|web|mobile|api)[^.;\n]{0,80}\bqa\b|\bsqa\b|\bsdet\b|"
    r"data\s+(?:analyst|scientist|engineer)|machine\s+learning|artificial\s+intelligence|generative\s+ai|"
    r"\bai\s+(?:engineer|developer|intern|trainee)|\bml\s+(?:engineer|developer|intern|trainee)|mlops|"
    r"cyber\s*security|soc\s+analyst|penetration\s+test|ethical\s+hacker|\biam\b|it\s+audit|"
    r"network\s+(?:engineer|administrator)|systems?\s+(?:engineer|administrator)|database\s+administrator|"
    r"\bit\s+(?:intern|trainee|support|specialist|officer|executive|engineer)|technical\s+support|service\s+desk|helpdesk|"
    r"devops|platform\s+engineer|cloud\s+(?:engineer|support|network)|site\s+reliability|"
    r"ui\s*/?\s*ux|ux\s+research|interaction\s+designer|product\s+designer|technical\s+product\s+manager|"
    r"it\s+business\s+analyst|solutions\s+engineer|pre[ -]?sales\s+engineer|technical\s+account|"
    r"scrum\s+master|it\s+project\s+manager|release\s+train\s+engineer|"
    r"low[ -]?code|no[ -]?code|wordpress|shopify|technical\s+writer|"
    r"robotics?|internet\s+of\s+things|\biot\b|computer\s+vision",
    re.I,
)
NON_CSE_TITLE_PATTERN = re.compile(
    r"marketing|sales|business development|human resources|\bhr\b|finance|account|audit|"
    r"content (?:writer|creation)|social media|brand|customer service|administration|admin|"
    r"civil|textile|mechanical|patient safety|medical|pharmacy|supply chain|merchandising",
    re.I,
)
SENIOR_TITLE_PATTERN = re.compile(
    r"\bsenior\b|\bsr\.?\b|\blead\b|principal|staff engineer|head of|director|chief|\bvp\b|"
    r"vice president|(?:engineer|developer)\s+(?:ii|iii|iv)\b",
    re.I,
)
MANAGEMENT_TITLE_PATTERN = re.compile(
    r"architect|manager|agile coach",
    re.I,
)
FRESHER_SIGNAL_PATTERN = re.compile(
    r"fresh(?:er| graduate)|entry[ -]?level|graduate (?:engineer|trainee|program)|"
    r"junior|associate|trainee|management trainee|\bmto\b|no experience|0\s*(?:-|to)\s*1\s*year|"
    r"seniority level\s+entry level",
    re.I,
)


CLOSED_POSTING_PATTERN = re.compile(
    r"no longer accepting applications|"
    r"this job is no longer available|"
    r"closed-job|"
    r"jobs?-closed|"
    r"applications? (?:are|is) closed|"
    r"position (?:has been )?filled",
    re.I,
)

# "18 years of age" and "3 years old" are not experience requirements.
AGE_CONTEXT_PATTERN = re.compile(r"\bage[ds]?\b|\bold\b|of age", re.I)
EXPERIENCE_CONTEXT_PATTERN = re.compile(
    r"experien|\bexp\.?\b|track record|working history|hands[ -]?on|professional",
    re.I,
)
YEARS_PATTERN = re.compile(
    r"(?P<lead>[^.;\n]{0,90}?)"
    r"(?:(?P<low>\d{1,2})\s*(?:-|to|–|—)\s*(?P<high>\d{1,2})|(?P<single>\d{1,2}))"
    r"\s*\+?\s*(?:years?|yrs?)"
    r"(?P<trail>[^.;\n]{0,60})",
    re.I,
)

# A posting demanding this many years or more is not an early-career role, no
# matter how much fresher-friendly boilerplate the description also carries.
EXPERIENCE_YEARS_CEILING = 3


def parse_min_experience_years(text: str) -> int | None:
    """Smallest number of years of experience the posting actually demands.

    Returns None when the text states no experience requirement. A range such as
    "3-5 years" reports 3, because that is the floor an applicant must clear.
    """
    cleaned = clean_markup(text)
    minimum: int | None = None
    for match in YEARS_PATTERN.finditer(cleaned):
        window = f"{match.group('lead') or ''} {match.group('trail') or ''}"
        if AGE_CONTEXT_PATTERN.search(window):
            continue
        if not EXPERIENCE_CONTEXT_PATTERN.search(window):
            continue
        years = int(match.group("single") or match.group("low"))
        if years > 40:  # Company boilerplate such as "45 years of experience in the market".
            continue
        if minimum is None or years < minimum:
            minimum = years
    return minimum


def is_closed_posting(markup: str) -> bool:
    """True when the source page says the posting stopped accepting applicants."""
    return bool(CLOSED_POSTING_PATTERN.search(markup))


def infer_category(title: str, description: str) -> str:
    rules = (
        ("Cybersecurity & Risk", r"cyber|security (?:analyst|engineer|architect)|soc analyst|penetration test|ethical hacker|identity and access|\biam\b|it audit|compliance analyst"),
        ("Product, Design & UI/UX", r"ui\s*/?\s*ux|user experience|ux research|interaction designer|product designer|technical product manager|product owner"),
        ("ITES, Support & Customer Success", r"\bit (?:support|specialist|officer|executive|engineer)|technical support|service desk|helpdesk|customer success|technical account|solutions engineer|pre[ -]?sales|it business analyst|process associate|\bbpo\b|\bkpo\b"),
        ("Project Management & Agile", r"scrum master|agile coach|it project manager|delivery manager|release train engineer"),
        ("AI, Data & Machine Learning", r"data (?:analyst|scientist|engineer)|machine learning|artificial intelligence|generative ai|\bai\b|\bml\b|\bllm\b|\brag\b|mlops|analytics engineer|prompt engineer|business intelligence|computer vision"),
        ("Cloud, Infrastructure & DevOps", r"devops|platform engineer|cloud (?:architect|engineer|support|network)|site reliability|\bsre\b|network (?:engineer|administrator)|systems? (?:engineer|administrator)|database administrator|\bdba\b"),
        ("Freelance & Niche Tech", r"low[ -]?code|no[ -]?code|wordpress|shopify|technical writer|green it"),
        ("Software Development & Engineering", r"software|developer|programmer|front[ -]?end|back[ -]?end|full[ -]?stack|web|mobile|android|ios|react native|flutter|laravel|django|\.net|java|python|salesforce|\bqa\b|\bsqa\b|\bsdet\b|quality assurance|test automation|embedded|firmware|game|\bunity\b|unreal|blockchain|web3|smart contract|spatial computing|\bar\s*/?\s*vr\b|robot|internet of things|\biot\b"),
    )
    for text in (title.lower(), f"{title} {description}".lower()):
        for category, pattern in rules:
            if re.search(pattern, text):
                return category
    return "Other CSE"


def is_cse_related(title: str, description: str) -> bool:
    title_match = bool(TECH_ROLE_PATTERN.search(title))
    if NON_CSE_TITLE_PATTERN.search(title) and not title_match:
        return False
    if title_match:
        return True
    text = f"{title} {description}"
    generic_early_career = re.search(r"intern|trainee|graduate|junior|associate|entry[ -]?level|\bmto\b", title, re.I)
    return bool(generic_early_career and GENERIC_TECH_ROLE_PATTERN.search(text))


def infer_job_type(title: str, description: str, hint: str = "", criteria: str = "") -> str:
    text = f"{title} {description} {criteria}"
    if re.search(r"\bintern(?:ship|s)?\b", title, re.I):
        return "Internship"
    if re.search(
        r"(?:job|employment)\s*(?:type|status)\s*(?::|-)?\s*[^.;\n]{0,30}\bintern(?:ship)?\b",
        description,
        re.I,
    ):
        return "Internship"
    if hint == "Internship" and re.search(r"\binternship\b", criteria, re.I):
        return "Internship"
    if re.search(
        r"(?:job|employment)\s*(?:type|status)\s*(?::|-)?\s*(?:full[ -]?time|part[ -]?time|contract)",
        description,
        re.I,
    ):
        return "Fresher job"
    if FRESHER_SIGNAL_PATTERN.search(text):
        return "Fresher job"
    return hint if hint in {"Internship", "Fresher job"} else "Fresher job"


def infer_experience_text(description: str) -> str:
    text = clean_markup(description)
    patterns = (
        r"fresh graduates? (?:are )?(?:encouraged|welcome|can apply)",
        r"no (?:prior )?experience (?:is )?required",
        r"\b0\s*(?:-|to)\s*[12]\s*years?(?: of experience)?",
        r"\b[12]\s*(?:-|to)\s*[12]\s*years?(?: of experience)?",
        r"(?:at least|minimum|min\.?|experience)\s*[012]\+?\s*years?",
        r"seniority level\s+entry level",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return re.sub(r"\s+", " ", match.group(0)).strip()[:100]
    return "Entry level indicated" if FRESHER_SIGNAL_PATTERN.search(text) else ""


def is_early_career(job: Job) -> bool:
    if SENIOR_TITLE_PATTERN.search(job.title):
        return False
    if MANAGEMENT_TITLE_PATTERN.search(job.title) and not re.search(
        r"\bjunior\b|\bassociate\b|\bassistant\b|trainee|graduate|intern", job.title, re.I
    ):
        return False

    text = f"{job.title} {job.description} {job.experience_text}"
    # A stated experience floor is hard evidence and outranks fresher wording.
    # A single "fresh graduates are encouraged" line used to keep three-year
    # roles in the feed as fresher jobs; the floor now wins.
    years = job.experience_years_min
    if years is None:
        years = parse_min_experience_years(text)
    if years is not None and years >= EXPERIENCE_YEARS_CEILING:
        return False
    return True


def infer_work_mode(location: str, description: str) -> str:
    text = f"{location} {description}".lower()
    if "hybrid" in text:
        return "Hybrid"
    if re.search(r"\bremote\b|work from home|wfh", text):
        return "Remote"
    return "On-site"


def infer_payment(title: str, description: str) -> tuple[str, str]:
    text = clean_markup(f"{title}. {description}")
    lower = text.lower()
    unpaid_patterns = (r"\bunpaid\b", r"without (?:a )?stipend", r"no (?:salary|stipend)")
    if any(re.search(pattern, lower) for pattern in unpaid_patterns):
        return "unpaid", "Unpaid"

    pay_patterns = (
        r"(?:salary|stipend|allowance|compensation|remuneration)\s*(?::|-|is)?\s*(?:bdt|tk\.?|taka)?\s*[\d,]+(?:\s*(?:-|to)\s*(?:bdt|tk\.?|taka)?\s*[\d,]+)?(?:\s*/?\s*(?:month|monthly))?",
        r"(?:bdt|tk\.?|taka)\s*[\d,]+(?:\s*(?:-|to)\s*(?:bdt|tk\.?|taka)?\s*[\d,]+)?",
        r"\bpaid internship\b",
        r"\bpaid intern\b",
        r"internship is paid",
        r"\binterns?\s*\([^)]*paid[^)]*\)",
    )
    for pattern in pay_patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            label = re.sub(r"\s+", " ", match.group(0)).strip(" .,-")
            if label.lower() in {"paid intern", "paid internship", "internship is paid"} or "paid" in label.lower() and "intern" in label.lower():
                label = "Paid internship"
            return "confirmed", label[:80]

    negotiable = re.search(r"salary\s*(?::|-|is)?\s*negotiable", text, re.I)
    if negotiable:
        return "likely", "Salary negotiable"

    benefit_words = ("lunch allowance", "travel allowance", "performance bonus")
    if any(word in lower for word in benefit_words):
        return "likely", "Allowance or benefit mentioned"
    return "unknown", "Pay not stated"


def infer_deadline(description: str) -> tuple[str, str, str]:
    """Return ISO date, source text, and open/expired/unknown status."""
    text = clean_markup(description)
    date_patterns = (
        r"(?:application|apply|submission|closing|last date|deadline)[^.;\n]{0,70}?(\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
        r"(?:application|apply|submission|closing|last date|deadline)[^.;\n]{0,70}?(\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]{3,9}\s+\d{4}|[A-Za-z]{3,9}\s+\d{1,2}(?:st|nd|rd|th)?(?:,\s*|\s+)\d{4})",
    )
    raw_date = ""
    for pattern in date_patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            raw_date = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", match.group(1), flags=re.I)
            break
    if not raw_date:
        return "", "Deadline not stated", "unknown"

    candidates = ("%d %B %Y", "%d %b %Y", "%B %d, %Y", "%b %d, %Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y")
    parsed = None
    for date_format in candidates:
        try:
            parsed = datetime.strptime(raw_date.strip(), date_format).date()
            break
        except ValueError:
            continue
    if parsed is None:
        return "", raw_date[:80], "unknown"
    status = "open" if parsed >= datetime.now(timezone.utc).date() else "expired"
    return parsed.isoformat(), raw_date[:80], status


# Ranking nudge per company tier. Kept small on purpose: a strong company is a
# tie-breaker, not a reason to bury a fresh, well-paid role at an unrated one.
COMPANY_TIER_BOOST = {"A": 18, "B": 10, "C": 3, "D": -25, "": 0}


def score_job(job: Job) -> int:
    score = 0
    if job.pay_status == "confirmed":
        score += 15
    elif job.pay_status == "likely":
        score += 8
    elif job.pay_status == "unpaid":
        score -= 20
    if job.posted_at:
        try:
            posted = datetime.fromisoformat(job.posted_at.replace("Z", "+00:00"))
            if posted.tzinfo is None:
                posted = posted.replace(tzinfo=timezone.utc)
            age_days = max(0, (datetime.now(timezone.utc) - posted).days)
            score += max(0, 30 - age_days * 2)
        except ValueError:
            pass
    if job.work_mode in {"Remote", "Hybrid"}:
        score += 5
    if job.description:
        score += 5
    if job.deadline_status == "open":
        score += 8
    elif job.deadline_status == "expired":
        score -= 80
    if job.posting_status == "closed":
        score -= 100
    # Company reputation nudges the ranking; it never dominates freshness or pay.
    score += COMPANY_TIER_BOOST.get(job.company_tier, 0)
    score -= 8 * len(job.company_flags)
    return score


def parse_linkedin_cards(markup: str, collected_at: str, job_type_hint: str) -> list[Job]:
    jobs: list[Job] = []
    for card in re.findall(r"<li>(.*?)</li>", markup, flags=re.I | re.S):
        url_match = re.search(
            r'class="[^"]*base-card__full-link[^"]*"\s+href="([^"]+)"', card, re.I
        )
        if not url_match:
            continue
        url = normalize_url(url_match.group(1))
        job_id_match = re.search(r"-(\d{7,})(?:\?|$)", url)
        job_id = job_id_match.group(1) if job_id_match else hashlib.sha1(url.encode()).hexdigest()[:16]
        title = first_match(r'base-search-card__title[^>]*>(.*?)</h3>', card)
        company = first_match(r'base-search-card__subtitle[^>]*>(.*?)</h4>', card)
        location = first_match(r'job-search-card__location[^>]*>(.*?)</span>', card)
        posted = first_match(r'<time[^>]+datetime="([^"]+)"', card)
        logo_match = re.search(r'(?:data-delayed-url|src)="([^"]+)"', card, re.I)
        logo = html.unescape(logo_match.group(1)) if logo_match else ""
        if not title or not company:
            continue
        jobs.append(
            Job(
                id=f"linkedin-{job_id}",
                title=title,
                company=company,
                location=location or "Bangladesh",
                url=url,
                source="LinkedIn",
                posted_at=posted,
                collected_at=collected_at,
                logo=logo,
                job_type=job_type_hint,
            )
        )
    return jobs


def collect_linkedin() -> tuple[list[Job], dict]:
    collected_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    jobs_by_id: dict[str, Job] = {}
    errors: list[str] = []

    pages_checked = 0
    for keywords, job_type_hint, experience_filter in SEARCHES:
        consecutive_rate_limits = 0
        for start in SEARCH_STARTS:
            params = urllib.parse.urlencode(
                {
                    "keywords": keywords,
                    "location": "Bangladesh",
                    "start": start,
                    "sortBy": "DD",
                    "f_E": experience_filter,
                }
            )
            url = f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?{params}"
            try:
                page_jobs = parse_linkedin_cards(fetch(url), collected_at, job_type_hint)
                pages_checked += 1
                consecutive_rate_limits = 0
                if not page_jobs:
                    break
                for job in page_jobs:
                    jobs_by_id[job.id] = job
            except RateLimitError as exc:
                errors.append(f"{keywords} page {start}: {exc}")
                consecutive_rate_limits += 1
                # The keyword is throttled; one more page will not fix that. After
                # repeated rate-limit failures give up on its remaining pages and
                # move on to the next keyword instead of burning the scan budget.
                if consecutive_rate_limits >= 2:
                    errors.append(f"{keywords}: rate limited repeatedly; skipped remaining result pages")
                    break
            except Exception as exc:  # One failed query should not stop the refresh.
                errors.append(f"{keywords} page {start}: {exc}")
                break

    jobs = list(jobs_by_id.values())
    enrich_failures = enrich_jobs(jobs)
    collected_count = len(jobs)
    closed_count = sum(job.posting_status == "closed" for job in jobs)
    jobs = [
        job
        for job in jobs
        if job.posting_status != "closed"
        and is_cse_related(job.title, job.description)
        and is_early_career(job)
    ]
    # A scan that could not read most detail pages is degraded, not healthy: pay,
    # deadline, experience, and closed-posting status all come from those pages.
    degraded = collected_count > 0 and enrich_failures > collected_count // 2
    if not jobs:
        state = "error"
    elif degraded:
        state = "degraded"
    else:
        state = "ok"
    status = {
        "name": "LinkedIn Jobs",
        "status": state,
        "count": len(jobs),
        "closed_dropped": closed_count,
        "detail_fetch_failures": enrich_failures,
        "detail_fetch_attempted": collected_count,
        "message": (
            f"Checked {pages_checked} result pages; kept {len(jobs)} of {collected_count} "
            f"CSE early-career listings; dropped {closed_count} closed circulars. "
            f"Detail pages unavailable for {enrich_failures} of {collected_count} listings"
            + (
                "; pay, deadline, experience, and closed-posting status are unreliable for those."
                if degraded
                else "."
            )
            if jobs
            else "; ".join(errors)[:240]
        ),
    }
    return jobs, status


def fetch_job_detail(job: Job) -> tuple[str, str, str]:
    """Return description, criteria, and posting status for one listing.

    The posting status comes from the same detail page the description does, so
    detecting a closed circular costs no extra request.
    """
    numeric_id = job.id.removeprefix("linkedin-")
    detail_url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{numeric_id}"
    markup = fetch(detail_url, timeout=30, retries=3)
    description = first_match(
        r'<div[^>]+class="[^"]*description__text[^"]*"[^>]*>(.*?)</div>\s*</div>',
        markup,
    )
    if not description:
        description = first_match(r'<div[^>]+class="[^"]*description__text[^"]*"[^>]*>(.*?)</section>', markup)
    criteria = " ".join(
        clean_markup(value)
        for value in re.findall(r'<li class="description__job-criteria-item">(.*?)</li>', markup, re.I | re.S)
    )
    posting_status = "closed" if is_closed_posting(markup) else "open"
    return description[:DESCRIPTION_LIMIT], criteria[:1200], posting_status


def classify_job(job: Job, description: str, criteria: str = "") -> Job:
    """Derive every inferred field from detail-page text. No network access.

    Split out of enrich_one so the deterministic classifiers can be re-run on an
    already-fetched job, as ai_review.py backfill does. The order matters:
    score_job reads the fields set above it.
    """
    combined = f"{description} {criteria}"
    job.job_type = infer_job_type(job.title, description, job.job_type, criteria)
    job.experience_text = infer_experience_text(combined)
    job.experience_years_min = parse_min_experience_years(f"{job.title} {combined}")
    job.category = infer_category(job.title, combined)
    job.work_mode = infer_work_mode(job.location, combined)
    job.pay_status, job.pay_text = infer_payment(job.title, combined)
    job.deadline, job.deadline_text, job.deadline_status = infer_deadline(combined)
    job.score = score_job(job)
    return job


def enrich_one(job: Job) -> tuple[Job, bool]:
    """Fill in detail-page fields. Returns the job and whether the fetch worked.

    A failed detail fetch used to be swallowed silently, which left pay, deadline,
    experience, category, and posting status unreliable with no visible signal.
    The caller now counts failures so a throttled scan is reported instead of
    quietly producing a thin dataset.
    """
    fetch_ok = True
    try:
        description, criteria, posting_status = fetch_job_detail(job)
        job.description = description
        job.posting_status = posting_status
    except Exception:
        fetch_ok = False
        description = ""
        criteria = ""
    classify_job(job, description, criteria)
    return job, fetch_ok


def enrich_jobs(jobs: list[Job]) -> int:
    """Enrich every job. Returns how many detail fetches failed.

    Concurrency is deliberately modest. At eight workers LinkedIn timed out on the
    large majority of detail requests, which is worse than being slow: every
    downstream field falls back to title-only guessing.
    """
    failures = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=ENRICH_WORKERS) as executor:
        for _, fetch_ok in executor.map(enrich_one, jobs):
            if not fetch_ok:
                failures += 1
    return failures


def deduplicate(jobs: Iterable[Job]) -> list[Job]:
    seen: set[tuple[str, str, str]] = set()
    unique: list[Job] = []
    for job in sorted(jobs, key=lambda item: (item.score, item.posted_at), reverse=True):
        key = (
            re.sub(r"\W+", "", job.title.lower()),
            re.sub(r"\W+", "", job.company.lower()),
            re.sub(r"\W+", "", job.location.lower()),
        )
        if key not in seen:
            seen.add(key)
            unique.append(job)
    return unique


def load_previous_jobs() -> list[dict]:
    if not OUTPUT_FILE.exists():
        return []
    try:
        return json.loads(OUTPUT_FILE.read_text(encoding="utf-8")).get("jobs", [])
    except (json.JSONDecodeError, OSError):
        return []


def preserve_recent(previous: list[dict], current: list[Job]) -> list[Job]:
    """Carry forward previously collected records the latest scan did not return.

    Records survive at most 14 days and must still pass the closed-posting check,
    the CSE classifier, and the early-career rules; a stale dataset cannot keep a
    role alive that a fresh scan would now reject.
    """
    current_ids = {job.id for job in current}
    known_fields = set(Job.__dataclass_fields__)
    for raw in previous:
        if raw.get("id") in current_ids:
            continue
        if raw.get("posting_status") == "closed":
            continue  # Never resurrect a circular the source already closed.
        try:
            collected = datetime.fromisoformat(raw["collected_at"].replace("Z", "+00:00"))
            if (datetime.now(timezone.utc) - collected).days <= 14:
                raw["is_fresh"] = False
                job = Job(**{key: raw[key] for key in known_fields if key in raw})
                combined = job.description
                job.job_type = infer_job_type(job.title, combined, job.job_type)
                job.experience_text = job.experience_text or infer_experience_text(combined)
                job.experience_years_min = parse_min_experience_years(f"{job.title} {combined}")
                job.category = infer_category(job.title, combined)
                if is_cse_related(job.title, combined) and is_early_career(job):
                    current.append(job)
        except (KeyError, TypeError, ValueError):
            continue
    return current


def load_id_set(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        return set(json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError):
        return set()


def send_telegram(new_jobs: list[Job]) -> set[str]:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id or not new_jobs:
        return set()

    jobs = sorted(new_jobs, key=lambda job: (job.posted_at, job.score), reverse=True)
    header = f"{len(jobs)} new Bangladesh CSE internships/fresher jobs"
    batches: list[tuple[str, list[str]]] = []
    message = header
    message_ids: list[str] = []
    for job in jobs:
        pay = job.pay_text or "Pay not stated"
        block = f"{job.job_type}: {job.title}\n{job.company} | {job.location}\n{pay}\n{job.url}"
        if len(message) + len(block) + 2 > 3800 and message_ids:
            batches.append((message, message_ids))
            message = header
            message_ids = []
        message += f"\n\n{block}"
        message_ids.append(job.id)
    if message_ids:
        batches.append((message, message_ids))

    sent_ids: set[str] = set()
    for message, batch_ids in batches:
        body = urllib.parse.urlencode(
            {"chat_id": chat_id, "text": message, "disable_web_page_preview": "true"}
        ).encode()
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage", data=body, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=20):
                sent_ids.update(batch_ids)
        except urllib.error.URLError as exc:
            print(f"Telegram notification failed: {exc}", file=sys.stderr)
            break
    return sent_ids


def scan_status_line(statuses: list[dict]) -> str:
    """The one-line scan outcome CI greps for. Printed exactly once per run."""
    failures = sum(int(entry.get("detail_fetch_failures", 0)) for entry in statuses)
    attempted = sum(int(entry.get("detail_fetch_attempted", 0)) for entry in statuses)
    degraded = any(entry.get("status") == "degraded" for entry in statuses)
    if degraded:
        return f"SCAN_STATUS: DEGRADED ({failures}/{attempted} detail fetches failed)"
    return "SCAN_STATUS: OK"


def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    previous = load_previous_jobs()
    all_jobs: list[Job] = []
    statuses: list[dict] = []

    jobs, status = collect_linkedin()
    all_jobs.extend(jobs)
    statuses.append(status)

    if not all_jobs and previous:
        print(scan_status_line(statuses))
        print("Refresh returned no jobs; preserving the previous dataset.", file=sys.stderr)
        return 1
    if not all_jobs:
        # An empty first dataset is indistinguishable from a broken scan, so it
        # must never be written as if the feed were genuinely empty.
        print(scan_status_line(statuses))
        print(
            "Refresh returned no jobs and there is no previous dataset; "
            "refusing to write an empty payload.",
            file=sys.stderr,
        )
        return 1

    all_jobs = preserve_recent(previous, deduplicate(all_jobs))
    all_jobs = deduplicate(all_jobs)
    expired_removed = sum(job.deadline_status == "expired" for job in all_jobs)
    all_jobs = [job for job in all_jobs if job.deadline_status != "expired"]
    # collect_linkedin already dropped closed listings, so counting them here would
    # always report zero. Take the real figure from the source status and count only
    # what this second pass catches, such as a preserved record gone closed.
    closed_dropped_at_source = sum(int(entry.get("closed_dropped", 0)) for entry in statuses)
    closed_removed = closed_dropped_at_source + sum(
        job.posting_status == "closed" for job in all_jobs
    )
    all_jobs = [job for job in all_jobs if job.posting_status != "closed"]

    # Roles a previous AI review rejected stay rejected; do not re-ask every scan.
    rejected_ids = load_id_set(REJECTED_FILE)
    ai_rejected_removed = sum(job.id in rejected_ids for job in all_jobs)
    all_jobs = [job for job in all_jobs if job.id not in rejected_ids]

    # Company reputation must land before scoring, since the tier nudges the rank.
    registry = company_registry.CompanyRegistry.load()
    company_registry.annotate(all_jobs, registry)
    for job in all_jobs:
        job.score = score_job(job)

    seen_ids = load_id_set(SEEN_FILE)
    notified_ids = load_id_set(NOTIFIED_FILE)
    new_jobs = [job for job in all_jobs if job.id not in seen_ids]
    unnotified_jobs = [job for job in all_jobs if job.id not in notified_ids]
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    payload = {
        "generated_at": generated_at,
        "timezone": "Asia/Dhaka",
        "scan_status": "degraded"
        if any(entry.get("status") == "degraded" for entry in statuses)
        else "ok",
        "summary": {
            "total": len(all_jobs),
            "internships": sum(job.job_type == "Internship" for job in all_jobs),
            "fresher_jobs": sum(job.job_type == "Fresher job" for job in all_jobs),
            "confirmed_paid": sum(job.pay_status == "confirmed" for job in all_jobs),
            "likely_paid": sum(job.pay_status == "likely" for job in all_jobs),
            "fresh": sum(job.is_fresh for job in all_jobs),
            "sources": len({job.source for job in all_jobs}),
            "deadline_known": sum(job.deadline_status == "open" for job in all_jobs),
            "expired_removed": expired_removed,
            "closed_removed": closed_removed,
            "detail_fetch_failures": sum(
                int(entry.get("detail_fetch_failures", 0)) for entry in statuses
            ),
            "ai_rejected_removed": ai_rejected_removed,
            "ai_verified": sum(job.review_status == "verified" for job in all_jobs),
            "tier_a": sum(job.company_tier == "A" for job in all_jobs),
            "tier_b": sum(job.company_tier == "B" for job in all_jobs),
            "unrated_companies": len({job.company for job in all_jobs if not job.company_tier}),
            "companies_rated": len(registry),
        },
        "review": {
            "mode": "regex-only",
            "note": (
                "Deterministic classification. Run python ai_review.py queue and let "
                "Claude Code review the queue to upgrade this dataset."
            ),
        },
        "source_status": statuses,
        "source_directory": list(SOURCE_DIRECTORY),
        "jobs": [asdict(job) for job in all_jobs],
    }
    OUTPUT_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    JOBS_JS_FILE.write_text(
        "window.INTERNSHIP_DATA = " + json.dumps(payload, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )
    SEEN_FILE.write_text(
        json.dumps(sorted(seen_ids | {job.id for job in all_jobs}), indent=2), encoding="utf-8"
    )
    sent_ids = send_telegram(unnotified_jobs)
    NOTIFIED_FILE.write_text(
        json.dumps(sorted(notified_ids | sent_ids), indent=2), encoding="utf-8"
    )
    counts = payload["summary"]
    print(
        f"Saved {len(all_jobs)} CSE roles ({counts['internships']} internships, "
        f"{counts['fresher_jobs']} fresher jobs, {len(new_jobs)} newly collected) "
        f"to {OUTPUT_FILE}"
    )
    print(
        f"Removed {expired_removed} expired, {closed_removed} closed, "
        f"{ai_rejected_removed} previously AI-rejected roles. "
        f"{counts['companies_rated']} companies rated; "
        f"{counts['unrated_companies']} companies still unrated."
    )
    degraded = [entry for entry in statuses if entry.get("status") == "degraded"]
    if degraded:
        print(
            f"WARNING: {counts['detail_fetch_failures']} detail pages could not be read. "
            "Pay, deadline, experience, and closed-posting status are unreliable for those "
            "roles. Re-run the scan later.",
            file=sys.stderr,
        )
    print(scan_status_line(statuses))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
