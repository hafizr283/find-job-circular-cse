#!/usr/bin/env python3
"""Apply to an exported InternBD queue using a local, visible Playwright browser.

This is intentionally a local assistant. It never logs in, bypasses CAPTCHA,
answers screening questions, or sends CV/profile data to InternBD or GitHub.
Conventional public forms can be submitted automatically; uncertain pages are
left open and recorded as ``needs_manual``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


BLOCKED_HOSTS = ("linkedin.com", "bdjobs.com")
BLOCKED_TERMS = re.compile(r"captcha|recaptcha|verify you are human|sign in|log in|login|password|one[- ]time password|otp", re.I)
SUBMIT_SELECTORS = (
    "button[type='submit']",
    "input[type='submit']",
    "button:has-text('Submit application')",
    "button:has-text('Apply now')",
    "button:has-text('Submit')",
    "input[value*='Submit' i]",
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Could not read {path}: {exc}") from exc


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def is_blocked_page(url: str, text: str) -> str:
    host = urlparse(url).netloc.lower()
    if any(host == item or host.endswith("." + item) for item in BLOCKED_HOSTS):
        return f"unsupported host: {host}"
    if BLOCKED_TERMS.search(text[:100_000]):
        return "login or CAPTCHA detected"
    return ""


def first_visible(page, selectors: tuple[str, ...]):
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if locator.is_visible(timeout=700):
                return locator
        except Exception:
            continue
    return None


def fill_first(page, selectors: tuple[str, ...], value: str) -> bool:
    if not value:
        return False
    locator = first_visible(page, selectors)
    if not locator:
        return False
    try:
        locator.fill(value)
        return True
    except Exception:
        return False


def process_job(page, job: dict, profile: dict, cv_path: Path, submit: bool, screenshot_dir: Path) -> dict:
    result = {"id": job.get("id", ""), "title": job.get("title", ""), "company": job.get("company", ""), "url": job.get("url", ""), "status": "failed", "message": "", "finished_at": now()}
    try:
        page.goto(job["url"], wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(1200)
        text = page.locator("body").inner_text(timeout=5_000)
        blocked = is_blocked_page(page.url, text)
        if blocked:
            result.update(status="needs_manual", message=blocked)
            return result

        file_input = page.locator("input[type='file']").first
        if not file_input.count():
            result.update(status="needs_manual", message="no CV upload field found")
            return result
        file_input.set_input_files(str(cv_path))

        filled = 0
        filled += fill_first(page, ("input[name*='full' i]", "input[id*='full' i]", "input[autocomplete='name']"), profile.get("name", ""))
        filled += fill_first(page, ("input[type='email']", "input[name*='email' i]", "input[id*='email' i]"), profile.get("email", ""))
        filled += fill_first(page, ("input[type='tel']", "input[name*='phone' i]", "input[name*='mobile' i]", "input[id*='phone' i]"), profile.get("phone", ""))
        if profile.get("cover_letter"):
            fill_first(page, ("textarea[name*='cover' i]", "textarea[id*='cover' i]", "textarea[name*='message' i]"), profile["cover_letter"])

        submit_button = first_visible(page, SUBMIT_SELECTORS)
        if not submit_button:
            result.update(status="needs_manual", message=f"CV uploaded; filled {filled} contact fields, but no clear submit button")
            return result
        if not submit:
            result.update(status="ready", message=f"CV uploaded; filled {filled} contact fields (dry run)")
            return result

        submit_button.click()
        page.wait_for_timeout(1800)
        confirmation = page.locator("body").inner_text(timeout=5_000)
        if re.search(r"thank you|application (?:has been )?submitted|successfully applied|received your application", confirmation, re.I):
            result.update(status="submitted", message="confirmation text detected")
        else:
            result.update(status="needs_manual", message="submit clicked; confirmation was not detected")
    except Exception as exc:
        result["message"] = str(exc)[:300]
        result["status"] = "failed"
    finally:
        if result["status"] in {"failed", "needs_manual"}:
            screenshot_dir.mkdir(parents=True, exist_ok=True)
            path = screenshot_dir / f"{job.get('id', 'job')}.png"
            try:
                page.screenshot(path=str(path), full_page=True)
                result["screenshot"] = str(path)
            except Exception:
                pass
    result["finished_at"] = now()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Process an InternBD application queue locally.")
    parser.add_argument("--queue", type=Path, default=Path("internbd-apply-queue.json"))
    parser.add_argument("--profile", type=Path, default=Path("private/profile.json"))
    parser.add_argument("--cv", type=Path, required=True, help="Local CV PDF/DOCX path")
    parser.add_argument("--log", type=Path, default=Path("private/application-log.json"))
    parser.add_argument("--screenshots", type=Path, default=Path("private/application-screenshots"))
    parser.add_argument("--headless", action="store_true", help="Run without opening a browser")
    parser.add_argument("--dry-run", action="store_true", help="Fill forms but do not click Submit")
    args = parser.parse_args()

    if not args.cv.is_file():
        raise SystemExit(f"CV file does not exist: {args.cv}")
    queue = read_json(args.queue)
    profile = read_json(args.profile)
    jobs = queue.get("jobs", [])
    if not jobs:
        print("Queue is empty.")
        return 0

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SystemExit("Install the worker dependency first: python -m pip install -r requirements-apply.txt && playwright install chromium") from exc

    previous = read_json(args.log) if args.log.exists() else {"runs": []}
    submitted_ids = {
        item.get("id")
        for run in previous.get("runs", [])
        for item in run.get("results", [])
        if item.get("status") == "submitted" and item.get("id")
    }
    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=args.headless)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        for index, job in enumerate(jobs, 1):
            print(f"[{index}/{len(jobs)}] {job.get('title', 'Untitled')} - {job.get('company', '')}")
            if job.get("id") in submitted_ids:
                result = {"id": job.get("id", ""), "title": job.get("title", ""), "company": job.get("company", ""), "url": job.get("url", ""), "status": "skipped", "message": "already submitted in application log", "finished_at": now()}
                results.append(result)
                print("  skipped: already submitted in application log")
                continue
            result = process_job(page, job, profile, args.cv.resolve(), submit=not args.dry_run, screenshot_dir=args.screenshots)
            results.append(result)
            print(f"  {result['status']}: {result['message']}")
            if result["status"] == "needs_manual" and not args.headless:
                print("  Browser remains open for manual completion; continuing after 20 seconds.")
                time.sleep(20)
        browser.close()

    previous.setdefault("runs", []).append({"started_at": now(), "queue": str(args.queue), "results": results})
    write_json(args.log, previous)
    print(f"Saved application log to {args.log}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
