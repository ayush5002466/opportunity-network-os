
import unittest
from src.radar import clean, is_relevant


class RelevanceTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "keywords": ["backend", "research intern", "apprenticeship"],
            "exclude_keywords": ["senior", "staff engineer"],
        }

    def test_backend_role_matches(self):
        self.assertTrue(
            is_relevant(
                {"title": "Backend Engineer", "description": "Java"},
                self.config,
            )
        )

    def test_research_role_matches_description(self):
        self.assertTrue(
            is_relevant(
                {
                    "title": "Student Programme",
                    "description": "Research Intern - AI",
                },
                self.config,
            )
        )

    def test_unrelated_role_does_not_match(self):
        self.assertFalse(
            is_relevant(
                {"title": "Accountant", "description": "Finance and payroll"},
                self.config,
            )
        )

    def test_senior_role_is_excluded(self):
        self.assertFalse(
            is_relevant(
                {"title": "Senior Backend Engineer", "description": "Java"},
                self.config,
            )
        )

    def test_clean_keeps_only_compact_fields(self):
        job = {
            "id": "1",
            "company": "Example",
            "title": "Backend",
            "location": "Remote",
            "url": "https://example.com/job",
            "updated_at": "today",
            "source": "Greenhouse",
            "description": "Long description should not be stored",
        }

        result = clean(job)

        self.assertNotIn("description", result)
        self.assertEqual(result["title"], "Backend")

    def test_recruiter_role_is_excluded(self):
        self.assertFalse(
            is_relevant(
                {
                    "title": "University Recruiter",
                    "description": "Supports software engineering recruiting",
                },
                self.config,
            )
        )

    def test_solutions_architect_role_is_excluded(self):
        self.assertFalse(
            is_relevant(
                {
                    "title": "Specialist Solutions Architect, Billing",
                    "description": "Works with software engineering teams",
                },
                self.config,
            )
        )

    def test_research_intern_in_description_still_matches(self):
        self.assertTrue(
            is_relevant(
                {
                    "title": "Student Programme",
                    "description": "Research Intern - AI",
                },
                self.config,
            )
        )


if __name__ == "__main__":
    unittest.main()
