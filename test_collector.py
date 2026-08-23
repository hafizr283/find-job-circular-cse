import io
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import collector

from collector import (
    Job,
    decode_response,
    infer_category,
    infer_job_type,
    infer_payment,
    is_closed_posting,
    is_cse_related,
    is_early_career,
    parse_min_experience_years,
    score_job,
)


def make_job(title: str, description: str = "", hint: str = "Fresher job") -> Job:
    job_type = infer_job_type(title, description, hint)
    return Job(
        id="test",
        title=title,
        company="Example",
        location="Bangladesh",
        url="https://example.com/job",
        source="Test",
        posted_at="",
        collected_at=datetime.now(timezone.utc).isoformat(),
        description=description,
        job_type=job_type,
    )


class CollectorClassificationTests(unittest.TestCase):
    def test_role_families_are_recognized(self) -> None:
        titles = (
            "Full Stack Developer",
            "Software Development Engineer in Test (SDET)",
            "Embedded Systems Engineer",
            "Generative AI Engineer",
            "MLOps Engineer",
            "Platform Engineer",
            "SOC Analyst",
            "IT Auditor",
            "UX Researcher",
            "IT Business Analyst",
            "Technical Support Engineer",
            "Scrum Master",
            "Low-Code Developer",
            "Technical Writer",
        )
        for title in titles:
            with self.subTest(title=title):
                self.assertTrue(is_cse_related(title, "Seniority level Entry level"))

    def test_non_cse_internships_are_rejected(self) -> None:
        titles = (
            "Marketing Intern",
            "Human Resources Intern",
            "Finance Intern",
            "Content Creation Intern",
            "Business Development Intern",
        )
        for title in titles:
            with self.subTest(title=title):
                self.assertFalse(is_cse_related(title, "Fresh graduates are encouraged"))
        self.assertFalse(
            is_cse_related(
                "Paid Internship",
                "An excellent opportunity to gain hands-on industrial engineering experience.",
            )
        )
        self.assertFalse(
            is_cse_related(
                "Sustainability Development Intern",
                "You will be embedded in sustainability and ESG consulting engagements.",
            )
        )
        self.assertFalse(
            is_cse_related(
                "Junior Designer",
                "Create artwork with CorelDRAW and other design software.",
            )
        )

    def test_generic_trainee_with_an_it_opening_is_allowed(self) -> None:
        self.assertTrue(
            is_cse_related(
                "Trainee (Paid Internship) - Multiple Functions",
                "Available positions include IT Trainee and HR Trainee.",
            )
        )

    def test_senior_roles_are_not_fresher_jobs(self) -> None:
        self.assertFalse(is_early_career(make_job("Senior Software Engineer", "5 years experience")))
        self.assertFalse(is_early_career(make_job("Cloud Architect", "Seniority level Entry level")))
        self.assertFalse(is_early_career(make_job("IT Project Manager", "Seniority level Entry level")))
        self.assertFalse(is_early_career(make_job("Software Engineer II")))
        self.assertFalse(is_early_career(make_job("Lead QA Engineer Intern", hint="Internship")))

    def test_explicit_junior_management_role_is_allowed(self) -> None:
        self.assertTrue(is_early_career(make_job("Junior IT Project Manager")))

    def test_internships_are_allowed_even_for_advanced_role_names(self) -> None:
        job = make_job("Cloud Security Architect Intern", hint="Internship")
        self.assertEqual(job.job_type, "Internship")
        self.assertTrue(is_early_career(job))

    def test_job_type_is_inferred(self) -> None:
        self.assertEqual(infer_job_type("Backend Developer Intern", "", "Fresher job"), "Internship")
        self.assertEqual(infer_job_type("Associate Software Engineer", "", "Fresher job"), "Fresher job")
        self.assertEqual(
            infer_job_type("Software QA Engineer", "Job Type: Full-Time", "Internship"),
            "Fresher job",
        )

    def test_compensation_signals(self) -> None:
        self.assertEqual(infer_payment("Salesforce Interns (Paid)", ""), ("confirmed", "Paid internship"))
        self.assertEqual(infer_payment("Junior Developer", "Salary: Negotiable"), ("likely", "Salary negotiable"))
        self.assertEqual(infer_payment("Software Intern", "This is an unpaid role"), ("unpaid", "Unpaid"))

    def test_category_taxonomy(self) -> None:
        self.assertEqual(infer_category("MLOps Engineer", ""), "AI, Data & Machine Learning")
        self.assertEqual(infer_category("SOC Analyst", ""), "Cybersecurity & Risk")
        self.assertEqual(infer_category("Technical Support Engineer", ""), "ITES, Support & Customer Success")
        self.assertEqual(
            infer_category("Software QA Engineer", "Experience with AWS and cloud environments"),
            "Software Development & Engineering",
        )


class ExperienceFloorTests(unittest.TestCase):
    """A stated experience floor must beat fresher-friendly boilerplate."""

    def test_years_are_parsed_from_requirement_text(self) -> None:
        cases = {
            "At least 3 years of experience in QA automation": 3,
            "Minimum 5 years experience required": 5,
            "Experience: 2-4 years": 2,
            "We want 8+ years of hands-on Java experience": 8,
            "Candidates need 1 to 2 years of professional experience": 1,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse_min_experience_years(text), expected)

    def test_age_and_company_age_are_not_experience(self) -> None:
        self.assertIsNone(parse_min_experience_years("Applicants must be at least 18 years of age"))
        self.assertIsNone(parse_min_experience_years("Candidates aged 22 to 28 years may apply"))
        self.assertIsNone(
            parse_min_experience_years("We have served the market for 45 years of experience")
        )

    def test_no_requirement_returns_none(self) -> None:
        self.assertIsNone(parse_min_experience_years("Fresh graduates are encouraged to apply"))
        self.assertIsNone(parse_min_experience_years(""))

    def test_three_year_role_is_not_a_fresher_job(self) -> None:
        # The real regression: one fresher-friendly line used to keep this in the feed.
        description = (
            "Fresh graduates are encouraged to apply. "
            "Requirements: At least 3 years of experience in test automation."
        )
        self.assertFalse(is_early_career(make_job("QA Automation Engineer", description)))

    def test_one_year_role_is_still_a_fresher_job(self) -> None:
        description = "At least 1 year of experience with Laravel. Fresh graduates may apply."
        self.assertTrue(is_early_career(make_job("Junior Web Developer", description)))

    def test_explicit_field_beats_reparsing(self) -> None:
        job = make_job("Software Engineer", "No experience details here")
        job.experience_years_min = 6
        self.assertFalse(is_early_career(job))


class ClosedPostingTests(unittest.TestCase):
    def test_closed_markers_are_detected(self) -> None:
        markers = (
            "<p>No longer accepting applications</p>",
            '<figure class="closed-job">',
            "Applications are closed for this circular",
            "This position has been filled",
        )
        for markup in markers:
            with self.subTest(markup=markup):
                self.assertTrue(is_closed_posting(markup))

    def test_open_posting_is_not_flagged(self) -> None:
        self.assertFalse(is_closed_posting('<button class="apply-button">Apply now</button>'))

    def test_closed_posting_is_scored_out_of_contention(self) -> None:
        job = make_job("Software Engineer", "Fresh graduates welcome")
        open_score = score_job(job)
        job.posting_status = "closed"
        self.assertLess(score_job(job), open_score - 90)


class CompanyTierScoringTests(unittest.TestCase):
    def test_tier_nudges_but_does_not_dominate(self) -> None:
        base = make_job("Software Engineer", "Fresh graduates welcome")
        neutral = score_job(base)
        base.company_tier = "A"
        strong = score_job(base)
        base.company_tier = "D"
        weak = score_job(base)
        self.assertGreater(strong, neutral)
        self.assertLess(weak, neutral)
        self.assertLess(strong - neutral, 25)

    def test_red_flags_cost_points(self) -> None:
        base = make_job("Software Engineer", "Fresh graduates welcome")
        clean = score_job(base)
        base.company_flags = ["staffing-agency", "aggregator-repost"]
        self.assertLess(score_job(base), clean)


class DecodeResponseTests(unittest.TestCase):
    def test_windows_1252_punctuation_survives(self) -> None:
        # LinkedIn sometimes emits cp1252 bytes; a plain utf-8 decode corrupted these.
        raw = "Trainee – Domino’s Pizza".encode("cp1252")
        self.assertEqual(decode_response(raw), "Trainee – Domino’s Pizza")

    def test_utf8_is_preferred(self) -> None:
        raw = "Trainee – café".encode("utf-8")
        self.assertEqual(decode_response(raw, "utf-8"), "Trainee – café")

    def test_unknown_charset_falls_back(self) -> None:
        raw = "plain ascii".encode("utf-8")
        self.assertEqual(decode_response(raw, "not-a-real-charset"), "plain ascii")


class EnrichmentFailureTests(unittest.TestCase):
    """A throttled detail fetch must be counted, never silently swallowed.

    Enrichment used to catch every exception and move on, so a scan where most
    detail pages timed out produced a dataset whose pay, deadline, experience, and
    posting status were title-only guesses, with nothing in the output saying so.
    """

    def setUp(self) -> None:
        self._original = collector.fetch_job_detail

    def tearDown(self) -> None:
        collector.fetch_job_detail = self._original

    @staticmethod
    def _job(job_id: str = "linkedin-1") -> Job:
        return Job(
            id=job_id,
            title="Software Engineer",
            company="Example",
            location="Dhaka",
            url="https://example.com/job",
            source="LinkedIn",
            posted_at="",
            collected_at="",
        )

    def test_failed_fetch_is_reported_and_status_stays_unknown(self) -> None:
        def boom(job):
            raise RuntimeError("throttled")

        collector.fetch_job_detail = boom
        job, fetch_ok = collector.enrich_one(self._job())
        self.assertFalse(fetch_ok)
        # Never guess "open" from a failed fetch; unknown is the honest value.
        self.assertEqual(job.posting_status, "unknown")

    def test_successful_fetch_records_the_status(self) -> None:
        collector.fetch_job_detail = lambda job: ("Fresh graduates welcome.", "", "open")
        job, fetch_ok = collector.enrich_one(self._job())
        self.assertTrue(fetch_ok)
        self.assertEqual(job.posting_status, "open")

    def test_closed_status_survives_enrichment(self) -> None:
        collector.fetch_job_detail = lambda job: ("Fresh graduates welcome.", "", "closed")
        job, _ = collector.enrich_one(self._job())
        self.assertEqual(job.posting_status, "closed")

    def test_enrich_jobs_counts_failures(self) -> None:
        state = {"n": 0}

        def flaky(job):
            state["n"] += 1
            if state["n"] % 2:
                raise RuntimeError("throttled")
            return ("Fresh graduates welcome.", "", "open")

        collector.fetch_job_detail = flaky
        batch = [self._job(f"linkedin-{index}") for index in range(6)]
        self.assertEqual(collector.enrich_jobs(batch), 3)

    def test_all_failures_are_counted(self) -> None:
        collector.fetch_job_detail = lambda job: (_ for _ in ()).throw(RuntimeError("throttled"))
        batch = [self._job(f"linkedin-{index}") for index in range(4)]
        self.assertEqual(collector.enrich_jobs(batch), 4)


class FetchRetryTests(unittest.TestCase):
    """Rate limits back off exponentially; timeouts keep the linear schedule."""

    def setUp(self) -> None:
        self.sleeps = []
        sleep_patch = mock.patch.object(collector.time, "sleep", side_effect=self.sleeps.append)
        sleep_patch.start()
        self.addCleanup(sleep_patch.stop)
        urlopen_patch = mock.patch.object(collector.urllib.request, "urlopen")
        self.urlopen = urlopen_patch.start()
        self.addCleanup(urlopen_patch.stop)

    @staticmethod
    def http_error(code: int) -> Exception:
        return collector.urllib.error.HTTPError(
            "https://example.com", code, "nope", {}, io.BytesIO(b"")
        )

    def test_rate_limit_backs_off_exponentially_then_gives_up(self) -> None:
        self.urlopen.side_effect = self.http_error(429)
        with self.assertRaises(collector.RateLimitError):
            collector.fetch("https://example.com")
        # Two retries on the 5s -> 15s exponential schedule.
        self.assertEqual(self.sleeps, [5, 15])

    def test_403_is_treated_as_a_rate_limit(self) -> None:
        self.urlopen.side_effect = self.http_error(403)
        with self.assertRaises(collector.RateLimitError):
            collector.fetch("https://example.com")
        self.assertEqual(self.sleeps, [5, 15])

    def test_rate_limit_recovers_after_backoff(self) -> None:
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b"ok"
        response.headers.get_content_charset.return_value = "utf-8"
        self.urlopen.side_effect = [self.http_error(429), response]
        self.assertEqual(collector.fetch("https://example.com"), "ok")
        self.assertEqual(self.sleeps, [5])

    def test_timeout_keeps_linear_backoff(self) -> None:
        self.urlopen.side_effect = TimeoutError("slow")
        with self.assertRaises(RuntimeError) as ctx:
            collector.fetch("https://example.com")
        self.assertNotIsInstance(ctx.exception, collector.RateLimitError)
        self.assertEqual(self.sleeps, [1.5, 3.0])

    def test_other_http_errors_are_not_retried(self) -> None:
        self.urlopen.side_effect = self.http_error(404)
        with self.assertRaises(collector.urllib.error.HTTPError):
            collector.fetch("https://example.com")
        self.assertEqual(self.sleeps, [])


class ScanStatusTests(unittest.TestCase):
    def test_ok_when_no_source_is_degraded(self) -> None:
        statuses = [{"status": "ok", "detail_fetch_failures": 2, "detail_fetch_attempted": 9}]
        self.assertEqual(collector.scan_status_line(statuses), "SCAN_STATUS: OK")

    def test_degraded_reports_the_failure_ratio(self) -> None:
        statuses = [
            {"status": "degraded", "detail_fetch_failures": 7, "detail_fetch_attempted": 12}
        ]
        self.assertEqual(
            collector.scan_status_line(statuses),
            "SCAN_STATUS: DEGRADED (7/12 detail fetches failed)",
        )


class MainGuardTests(unittest.TestCase):
    """A broken first scan must never be written as an empty feed."""

    def _run_main_with_zero_jobs(self, previous: list) -> int:
        status = {
            "name": "LinkedIn Jobs",
            "status": "error",
            "count": 0,
            "closed_dropped": 0,
            "detail_fetch_failures": 3,
            "detail_fetch_attempted": 3,
            "message": "scan failed",
        }
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            patches = [
                mock.patch.object(collector, "collect_linkedin", return_value=([], status)),
                mock.patch.object(collector, "load_previous_jobs", return_value=previous),
                mock.patch.object(collector, "OUTPUT_FILE", tmp_path / "jobs.json"),
                mock.patch.object(collector, "JOBS_JS_FILE", tmp_path / "jobs.js"),
                mock.patch.object(collector, "SEEN_FILE", tmp_path / "seen.json"),
                mock.patch.object(collector, "NOTIFIED_FILE", tmp_path / "notified.json"),
            ]
            for patch in patches:
                patch.start()
                self.addCleanup(patch.stop)
            exit_code = collector.main()
            wrote_payload = (tmp_path / "jobs.json").exists()
        self.assertFalse(wrote_payload)
        return exit_code

    def test_zero_jobs_without_previous_dataset_exits_1_without_writing(self) -> None:
        self.assertEqual(self._run_main_with_zero_jobs(previous=[]), 1)

    def test_zero_jobs_with_previous_dataset_exits_1_without_writing(self) -> None:
        previous = [{
            "id": "linkedin-1",
            "title": "Software Engineer",
            "company": "Example",
            "location": "Dhaka",
            "url": "https://example.com/job",
            "source": "LinkedIn",
            "posted_at": "",
            "collected_at": datetime.now(timezone.utc).isoformat(),
        }]
        self.assertEqual(self._run_main_with_zero_jobs(previous=previous), 1)


if __name__ == "__main__":
    unittest.main()
