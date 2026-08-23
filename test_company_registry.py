import json
import tempfile
import unittest
from pathlib import Path

from company_registry import (
    HARD_FLAGS,
    CompanyRegistry,
    normalize_company,
    score_from_signals,
    tier_from_score,
)
import ai_review


class NormalizeTests(unittest.TestCase):
    def test_legal_forms_are_stripped(self) -> None:
        for raw, expected in {
            "Brain Station 23 PLC": "brain station 23",
            "bKash Limited": "bkash",
            "Therap (BD) Ltd.": "therap",
            "Pridesys IT Ltd.": "pridesys it",
            "  Spaced   Out  Co  ": "spaced out",
        }.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalize_company(raw), expected)

    def test_empty_input_is_safe(self) -> None:
        self.assertEqual(normalize_company(""), "")
        self.assertEqual(normalize_company(None), "")


class ScoringTests(unittest.TestCase):
    def test_each_group_is_clamped_to_its_ceiling(self) -> None:
        inflated = {
            "track_record": 999,
            "engineering": 999,
            "early_career": 999,
            "pay_transparency": 999,
            "reputation": 999,
        }
        self.assertEqual(score_from_signals(inflated), 100)

    def test_missing_and_junk_signals_score_zero(self) -> None:
        self.assertEqual(score_from_signals({}), 0)
        self.assertEqual(score_from_signals({"track_record": "abc"}), 0)

    def test_tier_thresholds(self) -> None:
        self.assertEqual(tier_from_score(75), "A")
        self.assertEqual(tier_from_score(74), "B")
        self.assertEqual(tier_from_score(55), "B")
        self.assertEqual(tier_from_score(54), "C")
        self.assertEqual(tier_from_score(35), "C")
        self.assertEqual(tier_from_score(34), "D")

    def test_hard_flag_forces_tier_d(self) -> None:
        for flag in sorted(HARD_FLAGS):
            with self.subTest(flag=flag):
                self.assertEqual(tier_from_score(100, [flag]), "D")

    def test_soft_flag_does_not_force_tier_d(self) -> None:
        self.assertEqual(tier_from_score(90, ["staffing-agency"]), "A")


class LookupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = CompanyRegistry(
            [
                {"name": "Hired", "score": 17, "flags": ["aggregator-repost"]},
                {"name": "Impala Intech", "score": 47, "flags": []},
                {"name": "Square Group", "score": 50, "flags": []},
                {"name": "Brain Station 23", "aliases": ["BS23"], "score": 88, "flags": []},
            ]
        )

    def test_exact_and_alias_match(self) -> None:
        self.assertEqual(self.registry.lookup("Brain Station 23 PLC")["name"], "Brain Station 23")
        self.assertEqual(self.registry.lookup("BS23")["name"], "Brain Station 23")

    def test_decorated_name_matches_on_whole_words(self) -> None:
        found = self.registry.lookup("Impala Intech - Software Development Agency")
        self.assertEqual(found["name"], "Impala Intech")

    def test_single_token_name_never_matches_inside_a_word(self) -> None:
        # "hired" must not be found inside "rehired".
        self.assertIsNone(self.registry.lookup("Rehired Corp"))

    def test_single_token_name_does_not_claim_a_longer_company(self) -> None:
        self.assertIsNone(self.registry.lookup("Hired Ninja Solutions"))
        self.assertIsNone(self.registry.lookup("Square Textiles Division."))

    def test_unrated_company_is_neutral_not_negative(self) -> None:
        rating = self.registry.rating_for("Some Company Nobody Rated")
        self.assertEqual(rating["tier"], "")
        self.assertEqual(rating["score"], 0)
        self.assertEqual(rating["flags"], [])

    def test_score_is_derived_when_absent(self) -> None:
        registry = CompanyRegistry(
            [{"name": "Signals Only", "signals": {"track_record": 20, "engineering": 20}}]
        )
        self.assertEqual(registry.rating_for("Signals Only")["score"], 40)

    def test_missing_registry_file_loads_empty(self) -> None:
        registry = CompanyRegistry.load(Path("does-not-exist-anywhere.json"))
        self.assertEqual(len(registry), 0)
        self.assertEqual(registry.rating_for("Anyone")["tier"], "")


class ReviewQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = CompanyRegistry([{"name": "Known Co", "score": 60, "flags": []}])

    def test_confident_job_needs_no_review(self) -> None:
        job = {
            "posting_status": "open",
            "experience_years_min": 0,
            "category": "Software Development & Engineering",
            "company": "Known Co",
            "title": "Junior Backend Developer",
            "description": "Some text",
        }
        self.assertEqual(ai_review.review_reasons(job, self.registry), [])

    def test_each_doubt_is_reported(self) -> None:
        job = {
            "posting_status": "unknown",
            "experience_years_min": None,
            "category": "Other CSE",
            "company": "Nobody Knows Ltd",
            "title": "Trainee - Multiple Functions",
            "description": "Some text",
        }
        reasons = ai_review.review_reasons(job, self.registry)
        self.assertIn("posting status not confirmed open", reasons)
        self.assertIn("no experience floor parsed from the description", reasons)
        self.assertIn("category unresolved", reasons)
        self.assertIn("company not in the reputation registry", reasons)
        self.assertIn("title bundles several unrelated roles", reasons)

    def test_already_verified_job_is_skipped(self) -> None:
        job = {
            "review_status": "verified",
            "review_version": ai_review.REVIEW_VERSION,
            "posting_status": "unknown",
            "category": "Other CSE",
            "company": "Nobody Knows Ltd",
            "title": "Trainee - Multiple Functions",
        }
        self.assertEqual(ai_review.review_reasons(job, self.registry), [])

    def test_stale_review_version_is_requeued(self) -> None:
        job = {
            "review_status": "verified",
            "review_version": ai_review.REVIEW_VERSION - 1,
            "posting_status": "unknown",
            "category": "Other CSE",
            "company": "Known Co",
            "title": "Backend Developer",
        }
        self.assertTrue(ai_review.review_reasons(job, self.registry))

    def test_priority_puts_the_most_doubtful_first(self) -> None:
        low = ({"score": 90}, ["one reason"])
        high = ({"score": 10}, ["one", "two", "three"])
        ordered = sorted([low, high], key=lambda pair: ai_review.queue_priority(*pair))
        self.assertIs(ordered[0], high)

    def test_coerce_years_rejects_junk_and_out_of_range(self) -> None:
        self.assertEqual(ai_review.coerce_years(3), 3)
        self.assertEqual(ai_review.coerce_years("2"), 2)
        self.assertEqual(ai_review.coerce_years(0), 0)
        self.assertIsNone(ai_review.coerce_years(None))
        self.assertIsNone(ai_review.coerce_years(""))
        self.assertIsNone(ai_review.coerce_years("many"))
        self.assertIsNone(ai_review.coerce_years(-1))
        self.assertIsNone(ai_review.coerce_years(99))


class MergeCompanyTests(unittest.TestCase):
    def test_new_company_is_added_with_a_derived_tier(self) -> None:
        companies = []
        result = ai_review.merge_company(
            {
                "name": "Fresh Rating Ltd",
                "signals": {
                    "track_record": 20,
                    "engineering": 20,
                    "early_career": 16,
                    "pay_transparency": 12,
                    "reputation": 12,
                },
            },
            companies,
        )
        self.assertEqual(result, "added")
        self.assertEqual(companies[0]["score"], 80)
        self.assertEqual(companies[0]["tier"], "A")
        self.assertEqual(companies[0]["rated_by"], "claude-code")

    def test_existing_company_is_updated_not_duplicated(self) -> None:
        companies = [{"name": "Fresh Rating Ltd", "score": 10, "tier": "D", "note": "old"}]
        result = ai_review.merge_company(
            {"name": "fresh rating ltd", "score": 60, "note": "revised"}, companies
        )
        self.assertEqual(result, "updated")
        self.assertEqual(len(companies), 1)
        self.assertEqual(companies[0]["score"], 60)
        self.assertEqual(companies[0]["note"], "revised")

    def test_hard_flag_overrides_a_high_score(self) -> None:
        companies = []
        ai_review.merge_company(
            {"name": "Charges Money Ltd", "score": 95, "flags": ["pay-to-apply"]}, companies
        )
        self.assertEqual(companies[0]["tier"], "D")

    def test_nameless_entry_is_skipped(self) -> None:
        companies = []
        self.assertEqual(ai_review.merge_company({"name": "   "}, companies), "skipped")
        self.assertEqual(companies, [])


class RegistryDataFileTests(unittest.TestCase):
    """Guard the generated registry against silent corruption."""

    @classmethod
    def setUpClass(cls) -> None:
        path = Path(__file__).resolve().parent / "data" / "companies.json"
        cls.available = path.exists()
        cls.payload = json.loads(path.read_text(encoding="utf-8")) if cls.available else {}

    def test_registry_has_a_useful_number_of_companies(self) -> None:
        if not self.available:
            self.skipTest("data/companies.json not generated")
        self.assertGreaterEqual(len(self.payload["companies"]), 250)

    def test_every_record_has_a_valid_tier_and_score(self) -> None:
        if not self.available:
            self.skipTest("data/companies.json not generated")
        for company in self.payload["companies"]:
            with self.subTest(company=company.get("name")):
                self.assertIn(company["tier"], {"A", "B", "C", "D"})
                self.assertGreaterEqual(company["score"], 0)
                self.assertLessEqual(company["score"], 100)
                self.assertTrue(company["name"].strip())

    def test_every_record_declares_provenance(self) -> None:
        if not self.available:
            self.skipTest("data/companies.json not generated")
        allowed = {"wikipedia", "clutch", "model-knowledge", "feed-observed"}
        for company in self.payload["companies"]:
            with self.subTest(company=company.get("name")):
                self.assertIn(company.get("source"), allowed)
                self.assertIn(company.get("confidence"), {"high", "medium", "low"})

    def test_names_are_unique(self) -> None:
        if not self.available:
            self.skipTest("data/companies.json not generated")
        names = [c["name"].strip().lower() for c in self.payload["companies"]]
        self.assertEqual(len(names), len(set(names)))

    def test_aggregators_are_flagged_and_tiered_down(self) -> None:
        if not self.available:
            self.skipTest("data/companies.json not generated")
        registry = CompanyRegistry(self.payload["companies"])
        for name in ("nextjobz", "Bdjobs.com"):
            with self.subTest(name=name):
                rating = registry.rating_for(name)
                self.assertEqual(rating["tier"], "D")
                self.assertIn("aggregator-repost", rating["flags"])


if __name__ == "__main__":
    unittest.main()
