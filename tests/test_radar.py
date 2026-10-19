import unittest

from src.radar import clean, is_relevant


class RelevanceTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "keywords": ["backend", "research intern", "apprenticeship"],
            "exclude_keywords": ["senior", "staff engineer"],
        }

    def test_backend_role_matches(self):
        self.assertTrue(is_relevant({"title": "Backend Engineer", "description": "Java"}, self.config))

    def test_research_role_matches_description(self):
        self.assertTrue(is_relevant({"title": "Student Programme", "description": "Research Intern - AI"}, self.config))

    def test_unrelated_role_does_not_match(self):
        self.assertFalse(is_relevant({"title": "Accountant", "description": "Finance and payroll"}, self.config))

    def test_senior_role_is_excluded(self):
        self.assertFalse(is_relevant({"title": "Senior Backend Engineer", "description": "Java"}, self.config))

    def test_clean_keeps_only_compact_fields(self):
        job = {"id": "1", "company": "Example", "title": "Backend", "location": "Remote",
               "url": "https://example.com/job", "updated_at": "today", "source": "Lever",
               "description": "long description should not be stored"}
        self.assertNotIn("description", clean(job))
        self.assertEqual(clean(job)["title"], "Backend")


if __name__ == "__main__":
    unittest.main()
