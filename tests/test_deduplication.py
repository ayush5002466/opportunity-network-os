import unittest

from src.radar import canonical_url, deduplicate_jobs


class TestDeduplication(unittest.TestCase):
    def make_job(self, job_id, url, title="Software Engineer Intern"):
        return {
            "id": job_id,
            "company": "Example",
            "title": title,
            "location": "India",
            "url": url,
            "source": "Test",
        }

    def test_removes_tracking_parameters(self):
        self.assertEqual(
            canonical_url("https://example.com/job?utm_source=test"),
            canonical_url("https://example.com/job"),
        )

    def test_removes_trailing_slash(self):
        self.assertEqual(
            canonical_url("https://example.com/job/"),
            canonical_url("https://example.com/job"),
        )

    def test_removes_fragment(self):
        self.assertEqual(
            canonical_url("https://example.com/job#apply"),
            canonical_url("https://example.com/job"),
        )

    def test_same_url_is_deduplicated(self):
        jobs = [
            self.make_job("a", "https://example.com/job"),
            self.make_job("b", "https://example.com/job?utm_source=test"),
        ]
        self.assertEqual(len(deduplicate_jobs(jobs)), 1)

    def test_distinct_requisition_urls_are_preserved(self):
        jobs = [
            self.make_job("a", "https://example.com/jobs/101"),
            self.make_job("b", "https://example.com/jobs/102"),
        ]
        self.assertEqual(len(deduplicate_jobs(jobs)), 2)

    def test_empty_url_is_not_kept(self):
        jobs = [self.make_job("a", "")]
        self.assertEqual(len(deduplicate_jobs(jobs)), 0)


if __name__ == "__main__":
    unittest.main()
