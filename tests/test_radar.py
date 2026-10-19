import unittest

from src.radar import is_relevant, clean


CONFIG = {
    "keywords": ["software", "engineering", "data", "AI"],
    "exclude_keywords": ["senior", "staff", "principal", "manager"],
}


def job(title, description=""):
    return {
        "id": "test:1",
        "company": "Example",
        "title": title,
        "description": description,
        "location": "India",
        "url": "https://example.com/job",
        "updated_at": "",
        "source": "Test",
    }


class TestRadar(unittest.TestCase):

    def test_ai_research_fellow(self):
        self.assertTrue(is_relevant(job("AI Research Fellow"), CONFIG))

    def test_entry_level_data_engineer(self):
        self.assertTrue(is_relevant(job("Entry-Level Data Engineer"), CONFIG))

    def test_backend_new_grad(self):
        self.assertTrue(is_relevant(job("Backend Software Engineer, New Grad"), CONFIG))

    def test_software_apprenticeship(self):
        self.assertTrue(is_relevant(job("Software Developer Apprenticeship"), CONFIG))

    def test_software_intern(self):
        self.assertTrue(is_relevant(job("Software Engineering Intern"), CONFIG))

    def test_junior_backend_developer(self):
        self.assertTrue(is_relevant(job("Junior Backend Developer"), CONFIG))

    def test_graduate_software_engineer(self):
        self.assertTrue(is_relevant(job("Graduate Software Engineer"), CONFIG))

    def test_machine_learning_intern(self):
        self.assertTrue(is_relevant(job("Machine Learning Intern"), CONFIG))

    def test_research_assistant(self):
        self.assertTrue(is_relevant(job("AI Research Assistant"), CONFIG))

    def test_reject_senior_engineer(self):
        self.assertFalse(is_relevant(job("Senior Software Engineer"), CONFIG))

    def test_reject_staff_engineer(self):
        self.assertFalse(is_relevant(job("Staff Software Engineer"), CONFIG))

    def test_reject_principal_engineer(self):
        self.assertFalse(is_relevant(job("Principal Engineer"), CONFIG))

    def test_reject_engineering_manager(self):
        self.assertFalse(is_relevant(job("Engineering Manager"), CONFIG))

    def test_reject_recruiter(self):
        self.assertFalse(is_relevant(job("Technical Recruiter"), CONFIG))

    def test_reject_solutions_architect(self):
        self.assertFalse(is_relevant(job("Solutions Architect"), CONFIG))

    def test_description_keyword_cannot_make_senior_job_relevant(self):
        self.assertFalse(
            is_relevant(
                job("Senior Engineer", "Entry-level software internship"),
                CONFIG,
            )
        )

    def test_reject_internal_product_engineer(self):
        self.assertFalse(is_relevant(job("Internal Product Engineer"), CONFIG))

    def test_reject_internal_identity_engineer(self):
        self.assertFalse(
            is_relevant(job("Software Engineer, Internal Identity"), CONFIG)
        )

    def test_reject_internal_audit_lead(self):
        self.assertFalse(
            is_relevant(job("Internal Audit Data Analytics Lead"), CONFIG)
        )

    def test_reject_phd_internship(self):
        self.assertFalse(is_relevant(job("PhD Data Scientist, Intern"), CONFIG))

    def test_reject_high_school_internship(self):
        self.assertFalse(
            is_relevant(job("High School Internship, Software Engineering"), CONFIG)
        )

    def test_keep_software_engineer_intern(self):
        self.assertTrue(is_relevant(job("Software Engineer Intern"), CONFIG))

    def test_keep_software_engineer_new_grad(self):
        self.assertTrue(is_relevant(job("Software Engineer, New Grad"), CONFIG))
    def test_clean_keeps_expected_fields(self):
        result = clean(job("Software Engineering Intern"))
        self.assertEqual(
            set(result),
            {"id", "company", "title", "location", "url", "updated_at", "source"},
        )

    def test_clean_omits_description(self):
        result = clean(job("Software Engineering Intern", "Long description"))
        self.assertNotIn("description", result)


if __name__ == "__main__":
    unittest.main()

