from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
import tempfile
import unittest
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import job_search as js


NOW = "2026-10-18T09:00:00+00:00"
SOURCE = {"provider": "greenhouse", "board": "example", "company": "Example"}


def posting(identity=1, title="Legal Counsel", location="Singapore", description="2+ years of legal experience. English and Korean. Work on AI governance."):
    return {"id": identity, "title": title, "location": {"name": location}, "absolute_url": f"https://careers.example/jobs/{identity}", "content": description}


def config(**criteria):
    return {"sources": [SOURCE], "criteria": {"monthly_net_floor_cny": 100, "maximum_required_experience_years": 4,
            "bonus_is_requirement": False, "preferred_locations": ["Singapore"], **criteria}, "verified_facts": {}}


def fact(value, timestamp=NOW):
    return {"value": value, "source_url": "https://careers.example/jobs/1", "verified_at": timestamp}


def verified(**extra):
    return {"monthly_net_cny": fact(100), "qualification_compatible": fact(True), "work_permit_support": fact(True), **extra}


def job_for(item=None):
    return js.normalize(SOURCE, {"jobs": [item or posting()]}, NOW)[0]


def classify(facts=None, item=None, **criteria):
    settings = config(**criteria)
    return js.classify(job_for(item), settings["criteria"], {"greenhouse:example:1": facts or {}})


class EligibilityTests(unittest.TestCase):
    def test_unknown_salary_and_visa_never_ready(self):
        row = classify()
        self.assertEqual(row["status"], "needs_verification")
        self.assertEqual(row["monthly_net_cny"], "unknown")
        self.assertEqual(row["work_permit_support"], "unknown")

    def test_verified_gates_allow_review_with_contextual_bonus_unknown(self):
        row = classify(verified())
        self.assertEqual(row["status"], "ready_for_review")
        self.assertEqual(row["guaranteed_bonus_months"], "unknown")
        self.assertTrue(row["contextual_unknowns"])

    def test_salary_below_floor_rejected(self):
        self.assertEqual(classify(verified(monthly_net_cny=fact(99)))["status"], "excluded_requirement")

    def test_gross_description_is_not_net_salary_evidence(self):
        row = classify(item=posting(description="2 years of legal experience. Salary CNY 1,000,000 gross plus discretionary bonus. Visa sponsorship available."))
        self.assertEqual(row["status"], "needs_verification")
        self.assertEqual(row["monthly_net_cny"], "unknown")
        self.assertEqual(row["work_permit_support"], "unknown")

    def test_no_assumed_currency_conversion(self):
        self.assertEqual(classify(verified(monthly_net_cny=fact("USD 100")))["monthly_net_cny"], "unknown")

    def test_qualification_incompatibility_rejected(self):
        self.assertEqual(classify(verified(qualification_compatible=fact(False)))["status"], "excluded_requirement")

    def test_foreign_onsite_needs_permit_even_when_compensation_verified(self):
        self.assertEqual(classify(verified(work_permit_support=fact(None)))["status"], "needs_verification")

    def test_explicit_no_sponsorship_rejected(self):
        self.assertEqual(classify(verified(work_permit_support=fact(False)))["status"], "excluded_requirement")

    def test_mainland_role_does_not_need_visa(self):
        row = classify(verified(work_permit_support=fact(None)), posting(location="Shanghai, China (Hybrid)"))
        self.assertEqual(row["status"], "ready_for_review")
        self.assertEqual(row["work_permit_support"], "not_required_for_mainland_location")

    def test_mixed_mainland_and_foreign_location_keeps_permit_gate(self):
        row = classify(verified(work_permit_support=fact(None)), posting(location="Shanghai; Tokyo"))
        self.assertEqual(row["status"], "needs_verification")

    def test_hong_kong_is_not_mainland(self):
        self.assertFalse(js.mainland_location("Hong Kong, China"))

    def test_optional_bonus_never_disqualifies(self):
        self.assertEqual(classify(verified(guaranteed_bonus_months=fact(0)), guaranteed_bonus_months_floor=2)["status"], "ready_for_review")

    def test_explicit_required_bonus_uses_strict_boundary(self):
        criteria = {"bonus_is_requirement": True, "guaranteed_bonus_months_floor": 2, "bonus_must_exceed_floor": True}
        self.assertEqual(classify(verified(guaranteed_bonus_months=fact(2)), **criteria)["status"], "excluded_requirement")
        self.assertEqual(classify(verified(guaranteed_bonus_months=fact(2.1)), **criteria)["status"], "ready_for_review")

    def test_stale_or_unattributed_evidence_not_accepted(self):
        stale = (datetime.fromisoformat(NOW) - timedelta(days=31)).isoformat()
        row = classify(verified(monthly_net_cny=fact(100, stale)))
        self.assertEqual(row["status"], "needs_verification")
        row = classify(verified(monthly_net_cny={"value": 100}))
        self.assertEqual(row["monthly_net_cny"], "unknown")

    def test_boolean_is_not_a_salary(self):
        self.assertEqual(classify(verified(monthly_net_cny=fact(True)))["monthly_net_cny"], "unknown")

    def test_unconfigured_salary_floor_never_ready(self):
        self.assertEqual(classify(verified(), monthly_net_floor_cny=None)["status"], "needs_verification")

    def test_exact_experience_boundary_and_range(self):
        self.assertEqual(classify(verified(), posting(description="4 years of legal experience"))["status"], "ready_for_review")
        self.assertEqual(classify(verified(), posting(description="5–7 years of legal experience"))["status"], "excluded_pqe")
        self.assertEqual(classify(verified(), posting(description="5+ years of experience"), over_experience_limit="stretch")["status"], "stretch_pqe")

    def test_upper_bound_is_not_minimum(self):
        self.assertEqual(js.experience_evidence("Up to 5 years of legal experience"), (None, []))

    def test_real_board_experience_wording(self):
        self.assertEqual(js.experience_evidence("At least 5 years of relevant experience in legal operations")[0], 5)
        self.assertEqual(js.experience_evidence("At least 12+ years of relevant employment legal experience")[0], 12)
        self.assertEqual(js.experience_evidence("4 years’ PQE")[0], 4)
        self.assertEqual(js.experience_evidence("At least 8 years of privacy and data protection legal experience")[0], 8)
        self.assertEqual(js.experience_evidence("10+ years of transactional IP experience")[0], 10)
        self.assertEqual(js.experience_evidence("At least 12 years of litigation experience")[0], 12)

    def test_actual_airwallex_nonbreaking_hyphen_pqe_phrases(self):
        regulatory = "Qualified lawyer with around 6–8 years of relevant post‑qualification experience"
        product = "5-10 years of post‑qualification experience"
        self.assertEqual(js.experience_evidence(regulatory), (6, ["6–8 years of relevant post‑qualification experience"]))
        self.assertEqual(js.experience_evidence(product), (5, [product]))
        self.assertEqual(classify(item=posting(description=regulatory))["status"], "excluded_pqe")
        self.assertEqual(classify(item=posting(description=product))["status"], "excluded_pqe")

    def test_unicode_hyphens_and_ranges_keep_lower_bound(self):
        for hyphen in "-‐‑‒–—―−﹘﹣－":
            with self.subTest(hyphen=hyphen):
                text = f"5{hyphen}10 years of post{hyphen}qualification experience"
                self.assertEqual(js.experience_evidence(text), (5, [text]))
        self.assertEqual(js.experience_evidence("3 years of relevant PQE")[0], 3)

    def test_missing_experience_needs_review_unless_compatibility_verified(self):
        self.assertEqual(classify(item=posting(description="Legal research and Korean contract drafting."))["status"], "needs_verification")

    def test_case_insensitive_role_and_language_matching(self):
        row = classify(item=posting(title="LEGAL COUNSEL", description="2 YEARS OF LEGAL EXPERIENCE. korean AND ENGLISH. artificial intelligence"))
        self.assertIn("legal", row["role_matches"])
        self.assertIn("korean", row["language_matches"])
        self.assertIn("artificial intelligence", row["ai_matches"])

    def test_ai_word_not_matched_inside_training(self):
        row = classify(item=posting(description="2 years of legal experience. Training staff."))
        self.assertEqual(row["ai_matches"], [])

    def test_language_keyword_is_signal_not_proficiency_claim(self):
        row = classify(item=posting(description="2 years of legal experience. Korean preferred."))
        self.assertIn("korean", row["language_matches"])
        self.assertEqual(row["qualification_compatible"], "unknown")

    def test_worldwide_fallback_is_kept(self):
        row = classify(item=posting(location="Nairobi, Kenya"))
        self.assertEqual(row["status"], "needs_verification")
        self.assertEqual(row["preferred_location_matches"], [])

    def test_engineer_with_footer_legal_terms_is_excluded(self):
        self.assertEqual(classify(item=posting(title="Software Engineer", description="Contact legal counsel about privacy policy."))["status"], "excluded_role")

    def test_configured_engineering_exclusion_beats_privacy_title_keyword(self):
        row = classify(item=posting(title="Software ENGINEER, Privacy"), exclude_title_keywords=["engineer"])
        self.assertEqual(row["status"], "excluded_title")
        self.assertEqual(row["excluded_title_matches"], ["engineer"])

    def test_title_exclusions_use_whole_terms(self):
        row = classify(item=posting(title="Engineering Counsel"), exclude_title_keywords=["engineer"])
        self.assertEqual(row["status"], "needs_verification")

    def test_multiword_title_exclusion_and_senior_kept_by_default(self):
        row = classify(item=posting(title="General Counsel, Privacy"), exclude_title_keywords=["general counsel"])
        self.assertEqual(row["status"], "excluded_title")
        self.assertEqual(classify(item=posting(title="Senior Legal Counsel"))["status"], "needs_verification")


class ProviderAndOutputTests(unittest.TestCase):
    def test_three_public_endpoint_formats(self):
        self.assertEqual(js.source_endpoint({"provider": "lever", "board": "example"}), "https://api.lever.co/v0/postings/example?mode=json")
        self.assertEqual(js.source_endpoint({"provider": "ashby", "board": "example"}), "https://api.ashbyhq.com/posting-api/job-board/example?includeCompensation=true")
        with self.assertRaises(ValueError):
            js.source_endpoint({"provider": "lever", "board": "../secret"})

    def test_greenhouse_updated_is_not_posted(self):
        row = js.normalize(SOURCE, {"jobs": [{**posting(), "updated_at": NOW}]}, NOW)[0]
        self.assertIsNone(row["published_at"])
        self.assertEqual(row["updated_at"], NOW)

    def test_entity_encoded_html_from_greenhouse_is_stripped(self):
        row = job_for(posting(description="&lt;h2&gt;Qualifications&lt;/h2&gt;&lt;p&gt;5 years of relevant experience&lt;/p&gt;"))
        self.assertEqual(row["description"], "Qualifications 5 years of relevant experience")
        self.assertEqual(js.experience_evidence(row["description"])[0], 5)

    def test_lever_sections_normalized(self):
        source = {"provider": "lever", "board": "example"}
        row = js.normalize(source, [{"id": "l1", "text": "Privacy Counsel", "hostedUrl": "https://jobs.lever.co/example/l1", "categories": {"location": "London"}, "description": "<p>Privacy</p>", "lists": [{"text": "Requirements", "content": "<li>3 years of legal experience</li>"}], "workplaceType": "hybrid"}], NOW)[0]
        self.assertIn("3 years of legal experience", row["description"])
        self.assertEqual(row["workplace_type"], "hybrid")

    def test_ashby_unlisted_removed_and_compensation_preserved_uninterpreted(self):
        source = {"provider": "ashby", "board": "example"}
        item = {"id": "a1", "title": "Legal Counsel", "location": "Tokyo", "jobUrl": "https://jobs.ashbyhq.com/example/a1", "descriptionPlain": "2 years of legal experience", "compensation": {"summary": "$100k gross"}, "isListed": True}
        rows = js.normalize(source, {"jobs": [item, {**item, "id": "a2", "isListed": False}]}, NOW)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_compensation"], {"summary": "$100k gross"})

    def test_duplicates_by_id_and_canonical_public_url(self):
        rows = [job_for(), job_for(), {**job_for(), "job_id": "lever:example:1", "source_ids": ["lever:example:1"], "source_url": "https://careers.example/jobs/1/?tracking=x#apply"}]
        unique = js.deduplicate(rows)
        self.assertEqual(len(unique), 1)
        self.assertEqual(unique[0]["source_ids"], ["greenhouse:example:1", "lever:example:1"])

    def test_all_failures_are_failed_run_not_zero_matches(self):
        def blocked(_):
            raise HTTPError("https://api.lever.co", 403, "Forbidden", {}, None)
        report = js.collect(config(), blocked, NOW)
        self.assertEqual(report["run_status"], "failed")
        self.assertEqual(report["successful_sources"], 0)
        self.assertEqual(report["failed_sources"], 1)
        self.assertEqual(report["sources"][0]["error"], "HTTPError HTTP 403")

    def test_empty_well_formed_board_is_success(self):
        report = js.collect(config(), lambda _: {"jobs": []}, NOW)
        self.assertEqual(report["run_status"], "success")
        self.assertEqual(report["successful_sources"], 1)

    def test_malformed_payload_is_source_failure(self):
        report = js.collect(config(), lambda _: {"error": "wrong board"}, NOW)
        self.assertEqual(report["failed_sources"], 1)
        self.assertEqual(report["run_status"], "failed")

    def test_partial_failure_is_explicit(self):
        settings = config()
        settings["sources"] = [SOURCE, {"provider": "lever", "board": "example"}]
        def fetch(url):
            if "lever" in url:
                raise OSError("network error")
            return {"jobs": [posting()]}
        report = js.collect(settings, fetch, NOW)
        self.assertEqual((report["run_status"], report["successful_sources"], report["failed_sources"]), ("partial", 1, 1))

    def test_no_enabled_sources_is_failed_run(self):
        settings = config()
        settings["sources"] = [{**SOURCE, "enabled": False}]
        report = js.collect(settings, lambda _: self.fail("Disabled source was fetched"), NOW)
        self.assertEqual(report["run_status"], "failed")

    def test_outputs_include_evidence_and_persist_newness(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state = root / "state" / "seen.json"
            report = js.collect(config(), lambda _: {"jobs": [posting()]}, NOW)
            js.save_outputs(report, root / "output", state)
            self.assertTrue(report["jobs"][0]["is_new"])
            self.assertEqual(json.loads((root / "output" / "jobs.json").read_text())["jobs"][0]["fetched_at"], NOW)
            second = js.collect(config(), lambda _: {"jobs": [posting()]}, NOW)
            js.save_outputs(second, root / "output", state)
            self.assertFalse(second["jobs"][0]["is_new"])
            self.assertIn("source_api_url", (root / "output" / "jobs.csv").read_text())

    def test_failed_run_report_and_no_seen_state_update(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            report = js.collect(config(), lambda _: {}, NOW)
            js.save_outputs(report, root, root / "state.json")
            self.assertFalse((root / "state.json").exists())
            self.assertIn("This is not a zero-match result", (root / "report.md").read_text())

    def test_csv_formula_injection_is_escaped(self):
        self.assertEqual(js.cell("=HYPERLINK('x')"), "'=HYPERLINK('x')")

    def test_future_or_timezone_naive_evidence_not_accepted(self):
        for timestamp in ("2026-11-01T00:00:00+00:00", "2026-10-18T00:00:00"):
            self.assertIsNone(js.verified_value({"x": fact(100, timestamp)}, "x", NOW, 30))


if __name__ == "__main__":
    unittest.main()
