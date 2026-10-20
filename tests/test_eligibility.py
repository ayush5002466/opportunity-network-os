
import unittest

from src.radar import assess_eligibility


def make_job(
    title="Software Engineer Intern",
    location="Bengaluru, India",
    url="https://example.com/jobs/123",
    description="",
):
    return {
        "title": title,
        "location": location,
        "url": url,
        "description": description,
    }


class TestEligibility(unittest.TestCase):

    def test_missing_requirements_need_review(self):
        result = assess_eligibility(make_job())
        self.assertEqual(result["status"], "needs_review")
        self.assertIn("graduation_year", result)
        self.assertIn("location_fit", result)
        self.assertIn("work_authorization", result)
        self.assertIn("experience_fit", result)

    def test_2027_mention_is_recorded_not_assumed_verified(self):
        result = assess_eligibility(
            make_job("Software Engineer Intern (2027)")
        )
        self.assertEqual(result["graduation_year"], "mentions_2027")
        self.assertEqual(result["status"], "needs_review")

    def test_india_location_is_recognized(self):
        result = assess_eligibility(
            make_job(location="Bengaluru, India")
        )
        self.assertEqual(
            result["location_fit"],
            "india_or_remote_india",
        )

    def test_non_india_location_is_flagged_for_review(self):
        result = assess_eligibility(
            make_job(location="Austin, TX")
        )
        self.assertEqual(
            result["location_fit"],
            "outside_india_or_unclear",
        )

    def test_no_sponsorship_restriction_is_detected(self):
        result = assess_eligibility(
            make_job(
                description="We are unable to sponsor visas."
            )
        )
        self.assertEqual(
            result["work_authorization"],
            "restriction_mentioned",
        )

    def test_sponsorship_mention_does_not_verify_eligibility(self):
        result = assess_eligibility(
            make_job(
                description="Visa sponsorship available."
            )
        )
        self.assertEqual(
            result["work_authorization"],
            "sponsorship_mentioned",
        )
        self.assertEqual(result["status"], "needs_review")

    def test_experience_requirement_is_flagged(self):
        result = assess_eligibility(
            make_job(
                "Software Engineer",
                description="Requires 3 years of experience.",
            )
        )
        self.assertEqual(
            result["experience_fit"],
            "experience_requirement_mentioned",
        )

    def test_missing_url_needs_review(self):
        result = assess_eligibility(
            make_job("Software Intern", url="")
        )
        self.assertEqual(result["status"], "needs_review")
        self.assertTrue(result["reasons"])

    def test_work_arrangement_is_not_mistaken_for_country(self):
        result = assess_eligibility(
            make_job(
                "Software Engineer Intern (2027)",
                location="In-Office",
            )
        )
        self.assertEqual(
            result["location_fit"],
            "location_unknown",
        )


if __name__ == "__main__":
    unittest.main()
