#!/usr/bin/env python3
"""Build data/companies.json, the Bangladesh company reputation registry.

Why a builder script instead of a hand-written JSON file
--------------------------------------------------------
The registry has roughly 250 records. Keeping the reasoning in code means the
scoring stays consistent, a sector-wide correction is a one-line change, and
every record carries honest provenance.

Provenance is deliberate. Each record states where it came from:

    wikipedia         Name and sector fetched from the Wikipedia list of
                      companies of Bangladesh.
    clutch            Name, city, and headcount band fetched from Clutch.
    model-knowledge   Written from the assistant knowledge of the Bangladesh
                      technology employer landscape. Useful, but not verified
                      against a live page, so confidence is low or medium.
    feed-observed     The name appears in data/jobs.json, so it matters now.

Records sourced from model-knowledge are the ones the AI review layer should
re-check first. Run python ai_review.py queue and the reviewer will be handed
any company a job actually depends on.

Scores use the rubric in company_registry.py. Sector defaults are intentionally
modest: a bank or a TV channel is a real employer, but it is a mixed bet for a
CSE graduate who wants to write software, and the tier should say so.

Usage
-----
    python seed_companies.py            Write data/companies.json.
    python seed_companies.py --check    Report what would change, write nothing.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from company_registry import score_from_signals, tier_from_score

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "data" / "companies.json"

SIGNAL_KEYS = ("track_record", "engineering", "early_career", "pay_transparency", "reputation")


def signals(track: int, eng: int, early: int, pay: int, rep: int) -> dict:
    return dict(zip(SIGNAL_KEYS, (track, eng, early, pay, rep)))


# ---------------------------------------------------------------------------
# Sector baselines for the bulk of the general company list.
#
# A CSE graduate joining a garment conglomerate or a television channel is
# usually joining a small internal IT team, not an engineering organisation.
# The baselines encode that without pretending those employers are bad.
# ---------------------------------------------------------------------------
SECTOR_DEFAULTS = {
    "telecom": signals(22, 14, 12, 10, 9),
    "mfs-fintech": signals(20, 18, 13, 11, 9),
    "bank": signals(22, 10, 10, 9, 8),
    "nbfi-finance": signals(18, 8, 7, 8, 7),
    "insurance": signals(18, 7, 6, 7, 6),
    "conglomerate": signals(20, 8, 8, 7, 7),
    "pharma": signals(20, 8, 8, 8, 8),
    "consumer-goods": signals(18, 7, 8, 7, 7),
    "retail": signals(16, 7, 6, 6, 7),
    "textiles": signals(18, 6, 6, 6, 6),
    "industrials": signals(18, 7, 6, 6, 6),
    "materials": signals(18, 7, 6, 6, 6),
    "energy-utilities": signals(22, 8, 8, 9, 7),
    "oil-gas": signals(22, 8, 8, 9, 7),
    "state-owned": signals(24, 7, 9, 8, 6),
    "media": signals(16, 7, 6, 5, 6),
    "publishing": signals(16, 7, 6, 5, 6),
    "broadcasting": signals(16, 7, 6, 5, 6),
    "airline": signals(16, 8, 7, 7, 6),
    "real-estate": signals(18, 7, 7, 7, 7),
    "transport-logistics": signals(18, 9, 8, 7, 7),
    "agriculture-food": signals(18, 7, 7, 6, 7),
    "healthcare": signals(18, 8, 8, 7, 8),
    "education": signals(16, 8, 9, 6, 7),
    "ratings-advisory": signals(18, 8, 8, 8, 8),
}

# ---------------------------------------------------------------------------
# Technology employers, scored individually. These are the records that decide
# whether the feed ranks a genuinely good first job above filler.
#
# Fields: name, aliases, sector, kind, signals, flags, note, source, confidence
# kind: product | product-service | service | outsourcing | bpo | agency |
#       telco | fintech | randd | consultancy | nonprofit | jobboard | staffing
# ---------------------------------------------------------------------------
TECH_EMPLOYERS = [
    {
        "name": "Samsung R&D Institute Bangladesh",
        "aliases": ["SRBD", "Samsung R&D Institute Bangladesh Ltd", "Samsung Bangladesh"],
        "sector": "randd", "kind": "randd",
        "signals": signals(24, 24, 17, 14, 14),
        "note": "Multinational R&D centre. Hardest hiring bar in the country and the strongest pay for graduates.",
        "source": "model-knowledge", "confidence": "medium",
    },
    {
        "name": "Therap (BD) Ltd.",
        "aliases": ["Therap BD", "Therap Services", "Therap"],
        "sector": "product", "kind": "product",
        "signals": signals(23, 23, 17, 14, 14),
        "note": "US healthcare product engineering built in Dhaka. Long-running graduate intake and top-quartile fresher pay.",
        "source": "model-knowledge", "confidence": "medium",
    },
    {
        "name": "Brain Station 23",
        "aliases": ["BS23", "Brain Station 23 PLC", "Brainstation 23"],
        "sector": "product-service", "kind": "product-service",
        "signals": signals(24, 21, 18, 12, 13),
        "note": "Largest Bangladeshi software firm. 250-999 staff per Clutch, structured internship and trainee tracks.",
        "source": "clutch", "confidence": "high",
    },
    {
        "name": "bKash Limited",
        "aliases": ["bKash"],
        "sector": "mfs-fintech", "kind": "fintech",
        "signals": signals(24, 20, 15, 13, 13),
        "note": "Dominant mobile financial service. Large in-house engineering group and competitive pay.",
        "source": "wikipedia", "confidence": "medium",
    },
    {
        "name": "Grameenphone",
        "aliases": ["GP", "Grameenphone Ltd", "GrameenPhone"],
        "sector": "telecom", "kind": "telco",
        "signals": signals(25, 16, 18, 13, 13),
        "note": "Telenor-affiliated market leader. Well known graduate and internship programmes with transparent pay bands.",
        "source": "wikipedia", "confidence": "high",
    },
    {
        "name": "Robi Axiata Limited",
        "aliases": ["Robi", "Robi Axiata"],
        "sector": "telecom", "kind": "telco",
        "signals": signals(23, 15, 16, 12, 11),
        "note": "Second largest operator. Runs graduate and digital programmes; solid pay for early-career staff.",
        "source": "wikipedia", "confidence": "medium",
    },
    {
        "name": "Banglalink Digital Communications",
        "aliases": ["Banglalink", "Banglalink Digital"],
        "sector": "telecom", "kind": "telco",
        "signals": signals(21, 14, 14, 11, 10),
        "note": "VEON operator with a named graduate programme.",
        "source": "wikipedia", "confidence": "medium",
    },
    {
        "name": "Optimizely Bangladesh",
        "aliases": ["Optimizely", "Episerver Bangladesh", "Episerver"],
        "sector": "product", "kind": "product",
        "signals": signals(21, 23, 15, 13, 13),
        "note": "Product engineering for a global SaaS platform. Strong engineering practice and pay.",
        "source": "model-knowledge", "confidence": "low",
    },
    {
        "name": "Cefalo Bangladesh Ltd.",
        "aliases": ["Cefalo"],
        "sector": "service", "kind": "service",
        "signals": signals(20, 21, 15, 13, 13),
        "note": "Norwegian-owned development centre. Reputation for engineering culture and above-market pay.",
        "source": "model-knowledge", "confidence": "medium",
    },
    {
        "name": "Enosis Solutions",
        "aliases": ["Enosis"],
        "sector": "outsourcing", "kind": "outsourcing",
        "signals": signals(21, 18, 16, 10, 12),
        "note": "US-facing engineering services, 250-999 staff per Clutch. Hires graduates in volume.",
        "source": "clutch", "confidence": "high",
    },
    {
        "name": "Dynamic Solution Innovators",
        "aliases": ["DSi", "Dynamic Solution Innovators Ltd"],
        "sector": "outsourcing", "kind": "outsourcing",
        "signals": signals(21, 17, 15, 10, 11),
        "note": "250-999 staff, 25-49 USD per hour per Clutch. Established graduate pipeline.",
        "source": "clutch", "confidence": "high",
    },
    {
        "name": "REVE Systems",
        "aliases": ["REVE Systems Ltd.", "REVE"],
        "sector": "product", "kind": "product",
        "signals": signals(21, 17, 14, 9, 11),
        "note": "Telecom and messaging products, 250-999 staff per Clutch.",
        "source": "clutch", "confidence": "high",
    },
    {
        "name": "Riseup Labs",
        "aliases": ["Riseup Labs Ltd"],
        "sector": "product-service", "kind": "product-service",
        "signals": signals(19, 15, 14, 9, 10),
        "note": "Games and applications, 250-999 staff per Clutch.",
        "source": "clutch", "confidence": "high",
    },
    {
        "name": "Itransition Bangladesh",
        "aliases": ["Itransition"],
        "sector": "outsourcing", "kind": "outsourcing",
        "signals": signals(21, 17, 13, 10, 11),
        "note": "Global outsourcing group with a Dhaka presence, 1000-9999 staff per Clutch.",
        "source": "clutch", "confidence": "medium",
    },
    {
        "name": "Musemind",
        "aliases": ["Musemind UI UX"],
        "sector": "agency", "kind": "agency",
        "signals": signals(15, 14, 11, 11, 10),
        "note": "Design agency, 50-249 staff, 50-99 USD per hour per Clutch. Good for UI/UX portfolios.",
        "source": "clutch", "confidence": "high",
    },
    {
        "name": "Devxhub",
        "aliases": ["DevXHub"],
        "sector": "service", "kind": "service",
        "signals": signals(13, 12, 11, 9, 8),
        "note": "Rajshahi-based, 50-249 staff per Clutch. One of the few rated firms outside Dhaka.",
        "source": "clutch", "confidence": "high",
    },
    {
        "name": "ZAAG Systems",
        "aliases": ["ZAAG SYSTEMS"],
        "sector": "service", "kind": "service",
        "signals": signals(12, 11, 10, 8, 8),
        "note": "10-49 staff per Clutch. Small shop; verify pay and hours before committing.",
        "source": "clutch", "confidence": "high",
    },
    {
        "name": "VISER X",
        "aliases": ["ViserX", "Viser X Limited"],
        "sector": "service", "kind": "service",
        "signals": signals(13, 11, 10, 8, 8),
        "note": "50-249 staff per Clutch.",
        "source": "clutch", "confidence": "high",
    },
    {
        "name": "Augmex Technologies",
        "aliases": ["Augmex"],
        "sector": "service", "kind": "service",
        "signals": signals(11, 11, 9, 9, 7),
        "note": "10-49 staff per Clutch.",
        "source": "clutch", "confidence": "high",
    },
    {
        "name": "Genesys Softwares",
        "aliases": ["Genesys Softwares LLC"],
        "sector": "service", "kind": "service",
        "signals": signals(12, 11, 9, 7, 7),
        "note": "50-249 staff per Clutch; rate not published.",
        "source": "clutch", "confidence": "medium",
    },
    {
        "name": "Kaz Software",
        "aliases": ["Kaz"],
        "sector": "service", "kind": "service",
        "signals": signals(19, 18, 14, 11, 12),
        "note": "Long-established boutique with a strong engineering reputation.",
        "source": "model-knowledge", "confidence": "low",
    },
    {
        "name": "SELISE",
        "aliases": ["Selise", "SELISE rockin software", "Selise Bangladesh"],
        "sector": "service", "kind": "service",
        "signals": signals(18, 17, 13, 11, 11),
        "note": "Swiss-owned engineering services with a Dhaka delivery centre.",
        "source": "model-knowledge", "confidence": "low",
    },
    {
        "name": "Vivasoft Limited",
        "aliases": ["Vivasoft"],
        "sector": "service", "kind": "service",
        "signals": signals(15, 15, 13, 9, 10),
        "note": "Mid-size services firm that hires juniors.",
        "source": "model-knowledge", "confidence": "low",
    },
    {
        "name": "Nascenia Limited",
        "aliases": ["Nascenia"],
        "sector": "service", "kind": "service",
        "signals": signals(17, 16, 13, 10, 11),
        "note": "Ruby and web engineering shop with an internship track.",
        "source": "model-knowledge", "confidence": "low",
    },
    {
        "name": "Pathao",
        "aliases": ["Pathao Limited", "Pathao Ltd"],
        "sector": "transport-logistics", "kind": "product",
        "signals": signals(17, 18, 13, 10, 10),
        "note": "Consumer superapp with real in-house product engineering.",
        "source": "wikipedia", "confidence": "medium",
    },
    {
        "name": "ShopUp",
        "aliases": ["ShopUp Bangladesh", "SHOPUP"],
        "sector": "product", "kind": "product",
        "signals": signals(15, 18, 12, 10, 9),
        "note": "B2B commerce platform, venture funded, in-house engineering.",
        "source": "model-knowledge", "confidence": "low",
    },
    {
        "name": "Chaldal Limited",
        "aliases": ["Chaldal"],
        "sector": "retail", "kind": "product",
        "signals": signals(16, 17, 13, 9, 9),
        "note": "Grocery commerce with its own engineering and logistics software.",
        "source": "model-knowledge", "confidence": "low",
    },
    {
        "name": "Nagad Limited",
        "aliases": ["Nagad"],
        "sector": "mfs-fintech", "kind": "fintech",
        "signals": signals(17, 15, 12, 10, 8),
        "note": "Mobile financial service competing with bKash.",
        "source": "model-knowledge", "confidence": "low",
    },
    {
        "name": "SSL Wireless",
        "aliases": ["SSL Wireless Limited", "Software Shop Limited"],
        "sector": "mfs-fintech", "kind": "product-service",
        "signals": signals(19, 15, 12, 9, 10),
        "note": "Payments and messaging infrastructure used across Bangladeshi banking.",
        "source": "model-knowledge", "confidence": "low",
    },
    {
        "name": "Tiger IT Bangladesh",
        "aliases": ["Tiger IT", "TigerIT"],
        "sector": "product", "kind": "product",
        "signals": signals(19, 16, 12, 10, 10),
        "note": "Biometric and identity systems; notable government-scale delivery.",
        "source": "model-knowledge", "confidence": "low",
    },
    {
        "name": "DataSoft Systems Bangladesh",
        "aliases": ["DataSoft", "DataSoft Systems"],
        "sector": "service", "kind": "product-service",
        "signals": signals(19, 14, 12, 9, 9),
        "note": "Long-established CMMI-appraised software house.",
        "source": "model-knowledge", "confidence": "low",
    },
    {
        "name": "Southtech Limited",
        "aliases": ["Southtech", "Southtech Group"],
        "sector": "service", "kind": "product-service",
        "signals": signals(19, 14, 12, 9, 9),
        "note": "Banking and financial software vendor.",
        "source": "model-knowledge", "confidence": "low",
    },
    {
        "name": "LEADS Corporation Limited",
        "aliases": ["LEADS Corporation", "Leads Corporation"],
        "sector": "service", "kind": "product-service",
        "signals": signals(19, 14, 12, 9, 9),
        "note": "Core banking and enterprise software supplier.",
        "source": "model-knowledge", "confidence": "low",
    },
    {
        "name": "Streams Tech",
        "aliases": ["Streams Tech Inc", "Streams Tech Limited"],
        "sector": "service", "kind": "service",
        "signals": signals(16, 14, 12, 9, 9),
        "note": "US-facing services firm with a Dhaka engineering team.",
        "source": "model-knowledge", "confidence": "low",
    },
    {
        "name": "ReliSource Technologies",
        "aliases": ["ReliSource", "Relisource Technologies Ltd"],
        "sector": "outsourcing", "kind": "outsourcing",
        "signals": signals(18, 15, 13, 9, 10),
        "note": "US-owned engineering services with a large Dhaka centre. Appears in the live feed.",
        "source": "feed-observed", "confidence": "medium",
    },
    {
        "name": "Intelligent Machines",
        "aliases": ["Intelligent Machines Limited", "iMachines"],
        "sector": "product", "kind": "product",
        "signals": signals(15, 17, 12, 9, 9),
        "note": "Applied AI and data science consultancy.",
        "source": "model-knowledge", "confidence": "low",
    },
    {
        "name": "Ollyo",
        "aliases": ["Themeum", "Ollyo Bangladesh"],
        "sector": "product", "kind": "product",
        "signals": signals(14, 16, 12, 10, 9),
        "note": "WordPress product company with global paying customers.",
        "source": "model-knowledge", "confidence": "low",
    },
    {
        "name": "weDevs",
        "aliases": ["weDevs Limited"],
        "sector": "product", "kind": "product",
        "signals": signals(15, 15, 12, 10, 9),
        "note": "WordPress plugin product company.",
        "source": "model-knowledge", "confidence": "low",
    },
    {
        "name": "WPDeveloper",
        "aliases": ["WP Developer"],
        "sector": "product", "kind": "product",
        "signals": signals(14, 15, 12, 10, 9),
        "note": "WordPress product company.",
        "source": "model-knowledge", "confidence": "low",
    },
    {
        "name": "Divine IT Limited",
        "aliases": ["Divine IT"],
        "sector": "service", "kind": "product-service",
        "signals": signals(16, 12, 11, 8, 8),
        "note": "ERP and enterprise software vendor.",
        "source": "model-knowledge", "confidence": "low",
    },
    {
        "name": "Aamra Technologies Limited",
        "aliases": ["aamra technologies", "Aamra Networks"],
        "sector": "service", "kind": "service",
        "signals": signals(18, 11, 10, 8, 8),
        "note": "Listed IT services and connectivity group.",
        "source": "model-knowledge", "confidence": "low",
    },
    {
        "name": "Genex Infosys Limited",
        "aliases": ["Genex Infosys", "Genex"],
        "sector": "bpo", "kind": "bpo",
        "signals": signals(17, 9, 12, 8, 7),
        "note": "Large BPO. Volume hiring of freshers; call-centre style work rather than engineering.",
        "source": "model-knowledge", "confidence": "low",
    },
    {
        "name": "Digicon Technologies",
        "aliases": ["Digicon", "Digicon Technologies PLC"],
        "sector": "bpo", "kind": "bpo",
        "signals": signals(16, 9, 11, 8, 7),
        "note": "BPO and contact centre operator.",
        "source": "model-knowledge", "confidence": "low",
    },
    {
        "name": "Pridesys IT Ltd.",
        "aliases": ["Pridesys", "Pridesys IT"],
        "sector": "service", "kind": "product-service",
        "signals": signals(14, 11, 11, 8, 8),
        "note": "ERP vendor. Appears in the live feed.",
        "source": "feed-observed", "confidence": "medium",
    },
    {
        "name": "Daffodil Software Limited",
        "aliases": ["Daffodil Software", "Daffodil Computers", "Daffodil Family"],
        "sector": "service", "kind": "service",
        "signals": signals(16, 10, 12, 7, 7),
        "note": "Part of the Daffodil group. Hires many juniors; verify pay, hours, and any training-fee arrangement.",
        "source": "feed-observed", "confidence": "medium",
    },
    {
        "name": "Skylark Soft Limited",
        "aliases": ["Skylark Soft", "Skylark Soft Limited [SLS]"],
        "sector": "service", "kind": "service",
        "signals": signals(13, 11, 10, 8, 8),
        "note": "Small services firm. Appears in the live feed.",
        "source": "feed-observed", "confidence": "low",
    },
    {
        "name": "SJ Innovation LLC",
        "aliases": ["SJ Innovation", "SJI"],
        "sector": "service", "kind": "service",
        "signals": signals(15, 13, 12, 9, 9),
        "note": "US-owned agency with a Sylhet and Dhaka presence. Appears in the live feed.",
        "source": "feed-observed", "confidence": "low",
    },
    {
        "name": "Impala Intech",
        "aliases": ["Impala Intech - Software Development Agency", "Impala Intech Limited"],
        "sector": "agency", "kind": "agency",
        "signals": signals(11, 11, 10, 8, 7),
        "note": "Small development agency. Appears in the live feed.",
        "source": "feed-observed", "confidence": "low",
    },
    {
        "name": "Next Solution Lab",
        "aliases": ["NextSolution Lab", "Next Solution Lab Ltd"],
        "sector": "service", "kind": "service",
        "signals": signals(12, 13, 11, 8, 8),
        "note": "AI and data services firm. Appears in the live feed.",
        "source": "feed-observed", "confidence": "low",
    },
    {
        "name": "THESOFTKING Limited",
        "aliases": ["The Soft King", "THESOFTKING"],
        "sector": "service", "kind": "service",
        "signals": signals(11, 10, 10, 7, 7),
        "note": "Small services firm. Appears in the live feed.",
        "source": "feed-observed", "confidence": "low",
    },
    {
        "name": "Apptriangle Limited",
        "aliases": ["Apptriangle", "App Triangle"],
        "sector": "service", "kind": "service",
        "signals": signals(11, 11, 10, 7, 7),
        "note": "Small app development firm. Appears in the live feed.",
        "source": "feed-observed", "confidence": "low",
    },
    {
        "name": "Bitmorpher Limited",
        "aliases": ["Bitmorpher"],
        "sector": "service", "kind": "service",
        "signals": signals(11, 12, 10, 7, 7),
        "note": "Small services firm. Appears in the live feed.",
        "source": "feed-observed", "confidence": "low",
    },
    {
        "name": "Gakk Media Limited",
        "aliases": ["Gakk Media"],
        "sector": "agency", "kind": "agency",
        "signals": signals(11, 10, 9, 7, 7),
        "note": "Digital agency. Appears in the live feed.",
        "source": "feed-observed", "confidence": "low",
    },
    {
        "name": "Flyte Solutions Ltd.",
        "aliases": ["Flyte Solutions"],
        "sector": "service", "kind": "service",
        "signals": signals(11, 11, 10, 7, 7),
        "note": "Small services firm. Appears in the live feed.",
        "source": "feed-observed", "confidence": "low",
    },
    {
        "name": "Polygon Technology",
        "aliases": ["Polygon Technology Ltd"],
        "sector": "service", "kind": "service",
        "signals": signals(11, 11, 10, 7, 7),
        "note": "Small services firm. Appears in the live feed.",
        "source": "feed-observed", "confidence": "low",
    },
    {
        "name": "KPMG Bangladesh",
        "aliases": ["KPMG", "Rahman Rahman Huq"],
        "sector": "ratings-advisory", "kind": "consultancy",
        "signals": signals(23, 12, 15, 11, 13),
        "note": "Global advisory network. Good route into IT audit, risk, and compliance careers.",
        "source": "feed-observed", "confidence": "medium",
    },
    {
        "name": "Speechify",
        "aliases": ["Speechify Inc"],
        "sector": "product", "kind": "product",
        "signals": signals(14, 18, 11, 12, 10),
        "note": "US product company hiring remotely. Verify that the contract and pay are genuinely remote-friendly.",
        "source": "feed-observed", "confidence": "low",
    },
    {
        "name": "Commure",
        "aliases": ["Commure Inc"],
        "sector": "healthcare", "kind": "product",
        "signals": signals(15, 18, 11, 12, 9),
        "note": "US health technology company hiring remotely.",
        "source": "feed-observed", "confidence": "low",
    },
    {
        "name": "Grameen HealthTech Limited",
        "aliases": ["Grameen HealthTech"],
        "sector": "healthcare", "kind": "product",
        "signals": signals(16, 12, 12, 8, 9),
        "note": "Part of the Grameen family of organisations. Appears in the live feed.",
        "source": "feed-observed", "confidence": "low",
    },
    {
        "name": "Noora Health",
        "aliases": ["Noora Health Bangladesh"],
        "sector": "healthcare", "kind": "nonprofit",
        "signals": signals(15, 11, 11, 9, 11),
        "note": "Health nonprofit. Mission-driven work; pay bands are usually modest.",
        "source": "feed-observed", "confidence": "low",
    },
    {
        "name": "W3 Engineers Ltd.",
        "aliases": ["W3 Engineers"],
        "sector": "service", "kind": "service",
        "signals": signals(14, 13, 11, 8, 8),
        "note": "Mesh networking and mobile engineering firm.",
        "source": "model-knowledge", "confidence": "low",
    },
    {
        "name": "Field Buzz",
        "aliases": ["Fieldbuzz", "mPower Social Enterprises"],
        "sector": "product", "kind": "product",
        "signals": signals(15, 14, 12, 9, 10),
        "note": "Field-operations software used across development programmes.",
        "source": "model-knowledge", "confidence": "low",
    },
    {
        "name": "Sheba Platform Limited",
        "aliases": ["Sheba.xyz", "Sheba Platform"],
        "sector": "product", "kind": "product",
        "signals": signals(14, 14, 11, 8, 8),
        "note": "Home services marketplace with in-house engineering.",
        "source": "model-knowledge", "confidence": "low",
    },
    {
        "name": "10 Minute School",
        "aliases": ["Ten Minute School"],
        "sector": "education", "kind": "product",
        "signals": signals(14, 14, 13, 8, 9),
        "note": "Edtech platform with product and data teams; visible internship intake.",
        "source": "model-knowledge", "confidence": "low",
    },
    {
        "name": "Shohoz",
        "aliases": ["Shohoz Limited"],
        "sector": "transport-logistics", "kind": "product",
        "signals": signals(13, 13, 10, 8, 7),
        "note": "Ticketing and ride platform.",
        "source": "model-knowledge", "confidence": "low",
    },
    {
        "name": "Truck Lagbe",
        "aliases": ["TruckLagbe", "Truck Lagbe Limited"],
        "sector": "transport-logistics", "kind": "product",
        "signals": signals(12, 13, 10, 8, 7),
        "note": "Freight marketplace.",
        "source": "model-knowledge", "confidence": "low",
    },
    {
        "name": "Bikroy.com",
        "aliases": ["Bikroy", "Bikroy.com Limited"],
        "sector": "product", "kind": "product",
        "signals": signals(17, 14, 11, 9, 9),
        "note": "Classifieds marketplace, part of an international group.",
        "source": "wikipedia", "confidence": "medium",
    },
    {
        "name": "Walton Digi-Tech Industries",
        "aliases": ["Walton", "Walton Group", "Walton Hi-Tech Industries"],
        "sector": "consumer-goods", "kind": "product",
        "signals": signals(22, 13, 13, 9, 9),
        "note": "Domestic electronics manufacturer with genuine firmware and embedded work.",
        "source": "wikipedia", "confidence": "medium",
    },
    {
        "name": "DBL Group",
        "aliases": ["DBL", "DBL Group ICT"],
        "sector": "conglomerate", "kind": "product-service",
        "signals": signals(22, 12, 11, 9, 9),
        "note": "Textile conglomerate with an ICT and semiconductor arm.",
        "source": "wikipedia", "confidence": "medium",
    },
    {
        "name": "Advanced Chemical Industries",
        "aliases": ["ACI", "ACI Limited", "Advanced Chemical Industries Limited"],
        "sector": "conglomerate", "kind": "product-service",
        "signals": signals(23, 10, 12, 9, 10),
        "note": "Large conglomerate with an internal IT and digital organisation.",
        "source": "wikipedia", "confidence": "medium",
    },
]

# ---------------------------------------------------------------------------
# Job boards, aggregators, and staffing accounts.
#
# These matter more than their count suggests: two of them account for over half
# the current feed. A posting under one of these names hides the real employer
# inside the description, so the AI reviewer must extract it before the role can
# be trusted or rated.
# ---------------------------------------------------------------------------
AGGREGATORS = [
    ("nextjobz", ["Next Jobz", "nextjobz.com"], ["aggregator-repost", "non-cse-bundle"],
     "Aggregator account. Reposts other employers circulars, often bundling unrelated non-CSE functions into one listing. The real employer is named inside the description."),
    ("Bdjobs.com", ["Bdjobs", "BDJobs.com Limited"], ["aggregator-repost"],
     "National job board. Postings under this name are reposts; the hiring company is inside the description."),
    ("Urban Job", ["UrbanJob"], ["aggregator-repost"], "Aggregator account."),
    ("Hire Feed", ["HireFeed"], ["aggregator-repost"], "Aggregator account."),
    ("Hired", [], ["aggregator-repost"], "Aggregator account."),
    ("SME Careers", [], ["aggregator-repost"], "Aggregator account."),
    ("Quik Hire Staffing", ["Quik Hire"], ["staffing-agency"], "Staffing agency; the employer is a client, not this company."),
    ("Crossing Hurdles", [], ["staffing-agency"], "Recruitment consultancy posting on behalf of clients."),
    ("Field Nation", [], ["staffing-agency"], "Contract marketplace for field technicians."),
    ("Wing Assistant", ["Wing"], ["staffing-agency"], "Offshore assistant staffing platform."),
    ("Freesistant", [], ["staffing-agency"], "Virtual assistant staffing platform."),
    ("Traideas", [], ["aggregator-repost"], "Aggregator account."),
    ("Lowkia", [], ["aggregator-repost"], "Aggregator account."),
    ("giopio", [], ["aggregator-repost"], "Aggregator account."),
]

# ---------------------------------------------------------------------------
# General Bangladeshi companies, name and sector fetched from the Wikipedia list
# of companies of Bangladesh. Scored from SECTOR_DEFAULTS.
# ---------------------------------------------------------------------------
GENERAL_COMPANIES = [
    ("A K Khan & Company", "conglomerate"),
    ("Aarong", "retail"),
    ("Abul Khair Group", "conglomerate"),
    ("Agrani Bank", "state-owned"),
    ("Airtel Bangladesh", "telecom"),
    ("Akij Group", "conglomerate"),
    ("Alim Industries", "industrials"),
    ("Asian TV", "broadcasting"),
    ("ATN Bangla", "broadcasting"),
    ("ATN News", "broadcasting"),
    ("Bangladesh Bank", "state-owned"),
    ("Bangladesh Betar", "state-owned"),
    ("Bangladesh Machine Tools Factory", "state-owned"),
    ("Bangladesh Petroleum Corporation", "state-owned"),
    ("Bangladesh Pratidin", "publishing"),
    ("Bangladesh Railway", "state-owned"),
    ("Bangladesh Shipping Corporation", "state-owned"),
    ("Bangladesh Telecommunications Company Limited", "state-owned"),
    ("Bangladesh Television", "state-owned"),
    ("Banglavision", "broadcasting"),
    ("Bashundhara Group", "conglomerate"),
    ("Bengal Group of Industries", "conglomerate"),
    ("Beximco", "conglomerate"),
    ("Beximco Pharmaceuticals", "pharma"),
    ("Bhorer Kagoj", "publishing"),
    ("Bismillah Group", "textiles"),
    ("BRAC Bank", "bank"),
    ("BSRM", "materials"),
    ("BTCL", "state-owned"),
    ("Building Technology & Ideas", "real-estate"),
    ("Channel 9", "broadcasting"),
    ("Channel 24", "broadcasting"),
    ("Channel i", "broadcasting"),
    ("City Group", "conglomerate"),
    ("Concord Group", "real-estate"),
    ("Confidence Group", "industrials"),
    ("Coopers", "consumer-goods"),
    ("Credit Rating Information and Services Limited", "ratings-advisory"),
    ("Daily Inqilab", "publishing"),
    ("DBC News", "broadcasting"),
    ("Deepto TV", "broadcasting"),
    ("Delta Brac Housing Finance", "nbfi-finance"),
    ("Desh TV", "broadcasting"),
    ("Dhaka Bank", "bank"),
    ("Dhaka Mass Transit Company Limited", "state-owned"),
    ("Dragon Group", "textiles"),
    ("Dutch-Bangla Bank", "bank"),
    ("Eastern Bank", "bank"),
    ("Eastern Housing", "real-estate"),
    ("Ekattor TV", "broadcasting"),
    ("Ekushey Television", "broadcasting"),
    ("Eskayef Bangladesh", "pharma"),
    ("EXIM Bank", "bank"),
    ("FMC Dockyard", "industrials"),
    ("Gazi Group", "conglomerate"),
    ("Gemcon Group", "conglomerate"),
    ("General Pharmaceuticals", "pharma"),
    ("Globe Janakantha Shilpa Paribar", "conglomerate"),
    ("Grameen Bank", "bank"),
    ("GTV", "broadcasting"),
    ("Habib Group", "conglomerate"),
    ("Ha-Meem Group", "textiles"),
    ("Hatil", "consumer-goods"),
    ("HRC Group", "conglomerate"),
    ("IDLC Asset Management", "nbfi-finance"),
    ("IDLC Finance", "nbfi-finance"),
    ("IDLC Investments", "nbfi-finance"),
    ("IDLC Securities", "nbfi-finance"),
    ("IFIC Bank", "bank"),
    ("Impress Group", "conglomerate"),
    ("Independent Television", "broadcasting"),
    ("Investment Corporation of Bangladesh", "state-owned"),
    ("Islami Bank Bangladesh", "bank"),
    ("Jaaz Multimedia", "media"),
    ("Jaijaidin", "publishing"),
    ("Jamuna Bank", "bank"),
    ("Jamuna Group", "conglomerate"),
    ("Jamuna Oil Company", "oil-gas"),
    ("Jamuna Television", "broadcasting"),
    ("Janakantha", "publishing"),
    ("Janata Bank", "state-owned"),
    ("Jiban Bima Corporation", "insurance"),
    ("Jugantor", "publishing"),
    ("Kaler Kantho", "publishing"),
    ("Kallol Group", "conglomerate"),
    ("Karnaphuli Group", "conglomerate"),
    ("Kazi Farms Group", "agriculture-food"),
    ("KDS Group", "textiles"),
    ("Khulna Shipyard", "state-owned"),
    ("M. M. Ispahani Limited", "conglomerate"),
    ("Maasranga Television", "broadcasting"),
    ("Manab Zamin", "publishing"),
    ("Meghna Group of Industries", "conglomerate"),
    ("Mohona TV", "broadcasting"),
    ("Nasir Group", "conglomerate"),
    ("Nassa Group", "conglomerate"),
    ("National Bank Limited", "bank"),
    ("Navana Group", "conglomerate"),
    ("New Age", "publishing"),
    ("News24", "broadcasting"),
    ("Novoair", "airline"),
    ("NTV", "broadcasting"),
    ("One Bank", "bank"),
    ("Orion Group", "conglomerate"),
    ("Otobi", "consumer-goods"),
    ("Padma Oil Company", "oil-gas"),
    ("Paradise Group of Industries", "conglomerate"),
    ("Partex Group", "conglomerate"),
    ("Petrobangla", "state-owned"),
    ("Power Grid Company of Bangladesh", "energy-utilities"),
    ("PRAN-RFL Group", "consumer-goods"),
    ("Pride Group", "textiles"),
    ("Prime Bank", "bank"),
    ("Prothom Alo", "publishing"),
    ("Pubali Bank", "bank"),
    ("Radio Foorti", "broadcasting"),
    ("Radio Today", "broadcasting"),
    ("Rahimafrooz", "conglomerate"),
    ("Rajshahi Krishi Unnayan Bank", "state-owned"),
    ("Rangs Group", "conglomerate"),
    ("Regent Power", "energy-utilities"),
    ("Renata Limited", "pharma"),
    ("RTV", "broadcasting"),
    ("Runner Automobiles", "consumer-goods"),
    ("Rupali Bank", "state-owned"),
    ("Sadharan Bima Corporation", "insurance"),
    ("Sajeeb Group", "conglomerate"),
    ("SA TV", "broadcasting"),
    ("Samakal", "publishing"),
    ("S. Alam Group of Industries", "conglomerate"),
    ("Sheltech", "real-estate"),
    ("Sikder Group", "conglomerate"),
    ("Singer Bangladesh", "consumer-goods"),
    ("Somoy TV", "broadcasting"),
    ("Sonali Bank", "state-owned"),
    ("Southeast Bank", "bank"),
    ("Square Group", "conglomerate"),
    ("Square Pharmaceuticals", "pharma"),
    ("STS Group", "healthcare"),
    ("Summit Group", "conglomerate"),
    ("T K Group of Industries", "conglomerate"),
    ("The ACME Laboratories", "pharma"),
    ("The Daily Ittefaq", "publishing"),
    ("The Daily Star", "publishing"),
    ("The Sangbad", "publishing"),
    ("Titas Gas", "state-owned"),
    ("Transcom Group", "conglomerate"),
    ("Trust Bank", "bank"),
    ("United Finance", "nbfi-finance"),
    ("US-Bangla Airlines", "airline"),
    ("Uttara Bank", "bank"),
    ("Western Marine Shipyard", "industrials"),
    ("Biman Bangladesh Airlines", "airline"),
    ("Bismillah Airlines", "airline"),
    ("Grameen Telecom", "telecom"),
    ("Meghna Group", "conglomerate"),
    ("Pragoti Industries", "state-owned"),
    ("Shyampur Sugar Mills", "state-owned"),
    ("Mask Associates", "industrials"),
    ("Alliance Holdings", "real-estate"),
    ("Anwar Group of Industries", "conglomerate"),
    ("Aftab Automobiles", "industrials"),
    ("Apex Footwear", "consumer-goods"),
    ("Bata Shoe Company Bangladesh", "consumer-goods"),
    ("Berger Paints Bangladesh", "materials"),
    ("British American Tobacco Bangladesh", "consumer-goods"),
    ("Marico Bangladesh", "consumer-goods"),
    ("Nestle Bangladesh", "consumer-goods"),
    ("Reckitt Benckiser Bangladesh", "consumer-goods"),
    ("Unilever Bangladesh", "consumer-goods"),
    ("Lafarge Holcim Bangladesh", "materials"),
    ("Linde Bangladesh", "materials"),
    ("Olympic Industries", "consumer-goods"),
    ("Bangladesh Export Import Company", "conglomerate"),
    ("GlaxoSmithKline Bangladesh", "pharma"),
    ("Incepta Pharmaceuticals", "pharma"),
    ("Radiant Pharmaceuticals", "pharma"),
    ("Healthcare Pharmaceuticals", "pharma"),
    ("Opsonin Pharma", "pharma"),
    ("Aristopharma", "pharma"),
    ("Nuvista Pharma", "pharma"),
    ("Bangladesh Steel Re-Rolling Mills", "materials"),
    ("Abul Khair Steel", "materials"),
    ("GPH Ispat", "materials"),
    ("Shah Cement", "materials"),
    ("Premier Cement", "materials"),
    ("Crown Cement", "materials"),
    ("Bengal Commercial Bank", "bank"),
    ("City Bank", "bank"),
    ("Standard Chartered Bangladesh", "bank"),
    ("HSBC Bangladesh", "bank"),
    ("Mutual Trust Bank", "bank"),
    ("BRAC", "healthcare"),
    ("Grameen Shakti", "energy-utilities"),
    ("Summit Communications", "telecom"),
    ("Fiber@Home", "telecom"),
    ("Link3 Technologies", "telecom"),
    ("Amber IT", "telecom"),
    ("Carnival Internet", "telecom"),
    ("Dot Internet", "telecom"),
    ("Teletalk Bangladesh", "state-owned"),
    ("Bangladesh Computer Council", "state-owned"),
    ("Bangladesh Hi-Tech Park Authority", "state-owned"),
    ("Bangladesh Rural Electrification Board", "state-owned"),
    ("Chittagong Port Authority", "state-owned"),
    ("Dhaka Electric Supply Company", "energy-utilities"),
    ("Dhaka Power Distribution Company", "energy-utilities"),
    ("Dhaka WASA", "state-owned"),
    ("Bangladesh Bridge Authority", "state-owned"),
]


def tech_record(entry: dict) -> dict:
    score = score_from_signals(entry["signals"])
    flags = entry.get("flags", [])
    return {
        "name": entry["name"],
        "aliases": entry.get("aliases", []),
        "domain": entry.get("domain", ""),
        "sector": entry["sector"],
        "type": entry["kind"],
        "score": score,
        "tier": tier_from_score(score, flags),
        "flags": flags,
        "note": entry.get("note", ""),
        "signals": entry["signals"],
        "source": entry.get("source", "model-knowledge"),
        "confidence": entry.get("confidence", "low"),
    }


def aggregator_record(name: str, aliases: list, flags: list, note: str) -> dict:
    # An aggregator is not judged as an employer at all. It scores low because a
    # listing under its name tells a job seeker nothing about who they would
    # work for, which is exactly the problem worth flagging.
    entry_signals = signals(8, 2, 2, 2, 3)
    score = score_from_signals(entry_signals)
    return {
        "name": name,
        "aliases": aliases,
        "domain": "",
        "sector": "jobboard",
        "type": "jobboard" if "aggregator-repost" in flags else "staffing",
        "score": score,
        "tier": tier_from_score(score, flags),
        "flags": flags,
        "note": note,
        "signals": entry_signals,
        "source": "feed-observed",
        "confidence": "high",
    }


def general_record(name: str, sector: str) -> dict:
    entry_signals = SECTOR_DEFAULTS.get(sector, signals(15, 7, 7, 6, 6))
    score = score_from_signals(entry_signals)
    return {
        "name": name,
        "aliases": [],
        "domain": "",
        "sector": sector,
        "type": "enterprise",
        "score": score,
        "tier": tier_from_score(score, []),
        "flags": [],
        "note": (
            f"Scored from the {sector} sector baseline, not from company-specific research. "
            "A CSE graduate here usually joins an internal IT team rather than a product group."
        ),
        "signals": entry_signals,
        "source": "wikipedia",
        "confidence": "low",
    }


def load_existing() -> list:
    """Companies already on disk, so a rebuild does not destroy AI-added ratings."""
    try:
        payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    companies = payload.get("companies", []) if isinstance(payload, dict) else payload
    return companies if isinstance(companies, list) else []


def build() -> dict:
    records = []
    seen = set()

    def add(record: dict) -> None:
        key = record["name"].strip().lower()
        if key in seen:
            return
        seen.add(key)
        records.append(record)

    for entry in TECH_EMPLOYERS:
        add(tech_record(entry))
    for name, aliases, flags, note in AGGREGATORS:
        add(aggregator_record(name, aliases, flags, note))
    for name, sector in GENERAL_COMPANIES:
        add(general_record(name, sector))

    # Ratings the AI review pass added are not in the tables above, so a plain
    # rebuild would silently delete them. Carry them through instead: the seed
    # tables win on names they own, and anything else survives untouched.
    carried = 0
    for existing in load_existing():
        name = (existing.get("name") or "").strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        records.append(existing)
        carried += 1

    tiers = {}
    sources = {}
    for record in records:
        tiers[record.get("tier", "?")] = tiers.get(record.get("tier", "?"), 0) + 1
        source = record.get("source", "unknown")
        sources[source] = sources.get(source, 0) + 1

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "strategy": "See company_registry.py for the five-group scoring rubric and the tier thresholds.",
        "provenance": {
            "wikipedia": "https://en.wikipedia.org/wiki/List_of_companies_of_Bangladesh",
            "clutch": "https://clutch.co/bd/developers",
            "model-knowledge": "Assistant knowledge of the Bangladesh technology employer landscape; unverified against a live page.",
            "feed-observed": "Name observed in data/jobs.json.",
        },
        "counts": {
            "total": len(records),
            "by_tier": tiers,
            "by_source": sources,
            "carried_from_previous": carried,
        },
        "companies": records,
    }


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the company reputation registry")
    parser.add_argument("--check", action="store_true", help="report counts without writing")
    args = parser.parse_args(argv)

    payload = build()
    counts = payload["counts"]
    print(f"Companies: {counts['total']}")
    print(f"By tier:   {counts['by_tier']}")
    print(f"By source: {counts['by_source']}")
    if args.check:
        print("Check only; data/companies.json not written.")
        return 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
