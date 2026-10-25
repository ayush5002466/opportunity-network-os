import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from src import radar


def response(payload):
    result = Mock()
    result.json.return_value = payload
    return result


def greenhouse_job(job_id, title, url):
    return {
        "id": job_id,
        "title": title,
        "absolute_url": url,
        "location": {"name": "Remote"},
        "updated_at": "2026-10-25T00:00:00Z",
        "content": "<p>Internship</p>",
    }


def lever_job(job_id, title, url):
    return {
        "id": job_id,
        "text": title,
        "hostedUrl": url,
        "categories": {"location": "Remote"},
        "createdAt": 1792886400000,
        "descriptionPlain": "Internship",
    }


class TestSourceFetchers(unittest.TestCase):
    def test_greenhouse_valid_response_is_parsed(self):
        job = greenhouse_job(123, "Java Intern", "https://example.com/123")
        with patch("src.radar.requests.get", return_value=response({"jobs": [job]})):
            result = radar.fetch_greenhouse("Example", "board")

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], "gh:board:123")
        self.assertEqual(result[0]["title"], "Java Intern")
        self.assertEqual(result[0]["description"], "internship")
        self.assertEqual(result[0]["source"], "Greenhouse")

    def test_lever_valid_response_is_parsed(self):
        with patch(
            "src.radar.requests.get",
            return_value=response([
                lever_job("abc", "Backend Developer Intern", "https://example.com/abc")
            ]),
        ):
            result = radar.fetch_lever("Example", "site")

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], "lever:site:abc")
        self.assertEqual(result[0]["title"], "Backend Developer Intern")
        self.assertEqual(result[0]["location"], "Remote")
        self.assertEqual(result[0]["source"], "Lever")

    def test_both_sources_accept_empty_responses(self):
        with patch("src.radar.requests.get", return_value=response({"jobs": []})):
            self.assertEqual(radar.fetch_greenhouse("Example", "board"), [])
        with patch("src.radar.requests.get", return_value=response([])):
            self.assertEqual(radar.fetch_lever("Example", "site"), [])

    def test_malformed_json_is_raised_for_both_sources(self):
        malformed = json.JSONDecodeError("bad payload", "", 0)
        for fetcher, args in (
            (radar.fetch_greenhouse, ("Example", "board")),
            (radar.fetch_lever, ("Example", "site")),
        ):
            with self.subTest(fetcher=fetcher.__name__):
                mocked_response = response(None)
                mocked_response.json.side_effect = malformed
                with patch("src.radar.requests.get", return_value=mocked_response):
                    with self.assertRaises(json.JSONDecodeError):
                        fetcher(*args)

    def test_unexpected_payload_shapes_are_rejected(self):
        cases = (
            (radar.fetch_greenhouse, ("Example", "board"), {"jobs": {}}),
            (radar.fetch_greenhouse, ("Example", "board"), []),
            (radar.fetch_lever, ("Example", "site"), {}),
            (radar.fetch_lever, ("Example", "site"), [None]),
            (
                radar.fetch_lever,
                ("Example", "site"),
                [{"id": "x", "categories": []}],
            ),
        )
        for fetcher, args, payload in cases:
            with self.subTest(fetcher=fetcher.__name__, payload=payload):
                with patch(
                    "src.radar.requests.get",
                    return_value=response(payload),
                ):
                    with self.assertRaises(radar.InvalidSourcePayload):
                        fetcher(*args)

    def test_http_timeout_and_other_request_errors_propagate(self):
        http_error = requests.HTTPError(
            "secret-bearing request URL",
            response=Mock(status_code=503),
        )
        errors = (
            http_error,
            requests.Timeout("secret-bearing request URL"),
            requests.ConnectionError("secret-bearing request URL"),
        )
        for fetcher, args in (
            (radar.fetch_greenhouse, ("Example", "board")),
            (radar.fetch_lever, ("Example", "site")),
        ):
            for error in errors:
                with self.subTest(fetcher=fetcher.__name__, error=type(error).__name__):
                    mocked_response = response([])
                    mocked_response.raise_for_status.side_effect = error
                    with patch(
                        "src.radar.requests.get",
                        return_value=mocked_response,
                    ):
                        with self.assertRaises(type(error)):
                            fetcher(*args)


class TestSavedJobMerging(unittest.TestCase):
    def merge(self, saved, fresh):
        with tempfile.TemporaryDirectory() as temp:
            state_path = Path(temp) / "jobs.json"
            database_path = Path(temp) / "tracker.db"
            state_path.write_text(json.dumps(saved), encoding="utf-8")
            with (
                patch.object(radar, "STATE", state_path),
                patch.object(radar, "DB", database_path),
            ):
                merged = radar.merge_saved_jobs(saved, fresh)
            self.assertFalse(database_path.exists())
            return merged

    def test_same_id_and_url_merge_to_one_record(self):
        saved = {"job-1": {"id": "job-1", "url": "https://example.com/job"}}
        fresh = {"job-1": {"id": "job-1", "url": "https://example.com/job"}}

        merged = self.merge(saved, fresh)

        self.assertEqual(list(merged), ["job-1"])

    def test_different_ids_with_same_url_merge_and_keep_saved_identity(self):
        saved = {
            "old-id": {
                "id": "old-id",
                "url": "https://example.com/job",
                "status": "applied",
            }
        }
        fresh = {
            "new-id": {
                "id": "new-id",
                "url": "https://example.com/job",
                "title": "Java Developer Intern",
            }
        }

        merged = self.merge(saved, fresh)

        self.assertEqual(list(merged), ["old-id"])
        self.assertEqual(merged["old-id"]["id"], "old-id")
        self.assertEqual(merged["old-id"]["status"], "applied")
        self.assertEqual(merged["old-id"]["title"], "Java Developer Intern")

    def test_equivalent_canonical_urls_merge(self):
        saved = {
            "old-id": {
                "id": "old-id",
                "url": "https://EXAMPLE.com/job/?utm_source=old",
            }
        }
        fresh = {
            "new-id": {
                "id": "new-id",
                "url": "https://example.com/job?utm_campaign=new",
            }
        }

        merged = self.merge(saved, fresh)

        self.assertEqual(list(merged), ["old-id"])

    def test_different_ids_and_urls_remain_separate(self):
        saved = {"old-id": {"id": "old-id", "url": "https://example.com/old"}}
        fresh = {"new-id": {"id": "new-id", "url": "https://example.com/new"}}

        merged = self.merge(saved, fresh)

        self.assertEqual(set(merged), {"old-id", "new-id"})

    def test_empty_fresh_fields_do_not_replace_saved_values(self):
        saved = {
            "job-1": {
                "id": "job-1",
                "url": "https://example.com/job",
                "title": "Saved title",
                "description": "Useful saved description",
                "tags": ["saved"],
                "metadata": {"retained": True},
            }
        }
        fresh = {
            "job-1": {
                "id": "job-1",
                "url": "https://example.com/job",
                "title": "",
                "description": None,
                "tags": [],
                "metadata": {},
                "location": "Remote",
            }
        }

        merged = self.merge(saved, fresh)

        self.assertEqual(merged["job-1"]["title"], "Saved title")
        self.assertEqual(
            merged["job-1"]["description"],
            "Useful saved description",
        )
        self.assertEqual(merged["job-1"]["tags"], ["saved"])
        self.assertEqual(merged["job-1"]["metadata"], {"retained": True})
        self.assertEqual(merged["job-1"]["location"], "Remote")

    def test_conflicting_saved_records_merge_deterministically(self):
        saved = {
            "first-id": {
                "id": "first-id",
                "url": "https://example.com/job",
                "title": "First saved title",
                "first_only": "preserved",
            },
            "second-id": {
                "id": "second-id",
                "url": "https://example.com/job?utm_source=tracker",
                "description": "Second saved description",
            },
        }
        fresh = {
            "new-id": {
                "id": "new-id",
                "url": "https://example.com/job",
                "title": "Current title",
            }
        }

        merged = self.merge(saved, fresh)

        self.assertEqual(list(merged), ["first-id"])
        self.assertEqual(merged["first-id"]["id"], "first-id")
        self.assertEqual(merged["first-id"]["title"], "Current title")
        self.assertEqual(merged["first-id"]["first_only"], "preserved")
        self.assertEqual(
            merged["first-id"]["description"],
            "Second saved description",
        )

    def test_missing_id_or_url_can_still_match_by_the_other_key(self):
        saved_by_id = {
            "job-1": {"id": "job-1", "url": "https://example.com/old"}
        }
        fresh_without_url = {
            "job-1": {"id": "job-1", "url": "", "title": "Updated"}
        }
        merged_by_id = self.merge(saved_by_id, fresh_without_url)
        self.assertEqual(list(merged_by_id), ["job-1"])
        self.assertEqual(merged_by_id["job-1"]["url"], "https://example.com/old")

        saved_by_url = {
            "old-id": {"id": "old-id", "url": "https://example.com/job"}
        }
        fresh_without_id = {
            "new-key": {"id": "", "url": "https://example.com/job"}
        }
        merged_by_url = self.merge(saved_by_url, fresh_without_id)
        self.assertEqual(list(merged_by_url), ["old-id"])
        self.assertEqual(merged_by_url["old-id"]["id"], "old-id")

    def test_record_without_id_or_url_is_preserved_without_overwrite(self):
        saved = {"old-id": {"id": "old-id", "url": "https://example.com/old"}}
        fresh = {"unidentified": {"id": "", "url": "", "title": "Incomplete"}}

        merged = self.merge(saved, fresh)

        self.assertEqual(set(merged), {"old-id", "unidentified"})


class TestDiscoveryDiagnostics(unittest.TestCase):
    def run_discovery_with_tracker(
        self,
        saved_state,
        existing_applications,
        fetched_job,
        notify_mock=None,
    ):
        with tempfile.TemporaryDirectory() as temp:
            state_path = Path(temp) / "jobs.json"
            database_path = Path(temp) / "tracker.db"
            state_path.write_text(json.dumps(saved_state), encoding="utf-8")
            config = {
                "greenhouse": [{"company": "Example", "token": "board"}],
                "lever": [],
                "exclude_keywords": [],
            }

            with patch.object(radar, "DB", database_path):
                connection = radar.db_conn()
                try:
                    for application in existing_applications:
                        connection.execute(
                            """
                            INSERT INTO applications
                                (job_id, title, company, url, status, notes,
                                 updated_at, deadline)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            application,
                        )
                    connection.commit()
                finally:
                    connection.close()

            mocked_response = response({"jobs": [fetched_job]})
            output = io.StringIO()
            notify_mock = notify_mock or Mock()
            with (
                patch.object(radar, "DB", database_path),
                patch.object(radar, "STATE", state_path),
                patch.object(radar, "load_config", return_value=config),
                patch.object(radar, "load_state", return_value=saved_state),
                patch(
                    "src.radar.requests.get",
                    return_value=mocked_response,
                ),
                patch.object(radar, "notify", notify_mock),
                contextlib.redirect_stdout(output),
            ):
                result = radar.discover()

            connection = sqlite3.connect(database_path)
            try:
                rows = connection.execute(
                    """
                    SELECT job_id, title, company, url, status, notes, deadline
                    FROM applications
                    ORDER BY job_id
                    """
                ).fetchall()
            finally:
                connection.close()

            saved_after = json.loads(state_path.read_text(encoding="utf-8"))
            return result, rows, saved_after, output.getvalue()

    def test_sqlite_existing_id_missing_from_json_is_not_duplicated(self):
        job = greenhouse_job(
            42,
            "Java Developer Intern",
            "https://example.com/job",
        )
        result, rows, saved, output = self.run_discovery_with_tracker(
            {},
            [
                (
                    "gh:board:42",
                    "Tracked Java Intern",
                    "Example",
                    "https://example.com/job",
                    "applied",
                    "Application notes",
                    "2026-10-01T00:00:00+00:00",
                    "2026-11-10",
                )
            ],
            job,
        )

        self.assertEqual(result, 0)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "gh:board:42")
        self.assertEqual(rows[0][4:], ("applied", "Application notes", "2026-11-10"))
        self.assertEqual(set(saved), {"gh:board:42"})
        self.assertIn("Already present in saved state: 1", output)
        self.assertIn("New listings: 0", output)

    def test_saved_record_id_match_is_not_counted_or_notified_as_new(self):
        job = greenhouse_job(
            42,
            "Java Developer Intern",
            "https://example.com/changed-job-url",
        )
        notify_mock = Mock()
        result, rows, saved, output = self.run_discovery_with_tracker(
            {
                "legacy-saved-key": {
                    "id": "gh:board:42",
                    "url": "https://example.com/previous-job-url",
                    "title": "Previously saved Java Intern",
                }
            },
            [],
            job,
            notify_mock=notify_mock,
        )

        self.assertEqual(result, 0)
        self.assertEqual(len(rows), 1)
        self.assertEqual(set(saved), {"legacy-saved-key"})
        self.assertIn("Already present in saved state: 1", output)
        self.assertIn("New listings: 0", output)
        self.assertIn("No new relevant listings.", output)
        notify_mock.assert_not_called()

    def test_sqlite_canonical_url_match_preserves_existing_application_fields(self):
        job = greenhouse_job(
            99,
            "Java Developer Intern",
            "https://example.com/job?utm_source=greenhouse",
        )
        result, rows, saved, output = self.run_discovery_with_tracker(
            {},
            [
                (
                    "legacy-job-id",
                    "Existing application title",
                    "Example",
                    "https://EXAMPLE.com/job/",
                    "interview",
                    "Keep these notes",
                    "2026-09-01T00:00:00+00:00",
                    "2026-11-20",
                )
            ],
            job,
        )

        self.assertEqual(result, 0)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "legacy-job-id")
        self.assertEqual(
            rows[0][1:3],
            ("Existing application title", "Example"),
        )
        self.assertEqual(rows[0][4:], ("interview", "Keep these notes", "2026-11-20"))
        self.assertEqual(set(saved), {"legacy-job-id"})
        self.assertEqual(saved["legacy-job-id"]["id"], "legacy-job-id")
        self.assertIn("Already present in saved state: 1", output)
        self.assertIn("New listings: 0", output)

    def test_sqlite_identity_reconciles_json_duplicates_without_losing_data(self):
        job = greenhouse_job(
            99,
            "Java Developer Intern",
            "https://example.com/job?utm_source=greenhouse",
        )
        saved_state = {
            "json-id-a": {
                "id": "json-id-a",
                "url": "https://example.com/job/",
                "saved_description": "Saved role description",
                "status": "applied",
            },
            "json-id-a-duplicate": {
                "id": "json-id-a-duplicate",
                "url": "https://EXAMPLE.com/job?utm_campaign=saved",
                "saved_note": "Useful detail from duplicate JSON record",
            },
            "unrelated-json": {
                "id": "unrelated-json",
                "url": "https://example.com/unrelated",
                "description": "Unrelated saved listing",
            },
        }
        result, rows, saved, output = self.run_discovery_with_tracker(
            saved_state,
            [
                (
                    "sqlite-id-b",
                    "Tracked application title",
                    "Example",
                    "https://EXAMPLE.com/job/",
                    "interview",
                    "Keep tracker notes",
                    "2026-09-01T00:00:00+00:00",
                    "2026-11-20",
                ),
                (
                    "sqlite-duplicate",
                    "Second tracker record",
                    "Example",
                    "https://example.com/job?utm_source=second",
                    "offer",
                    "Do not delete this existing record",
                    "2026-09-02T00:00:00+00:00",
                    "2026-11-21",
                ),
                (
                    "unrelated-sqlite",
                    "Unrelated application",
                    "Other",
                    "https://example.com/other",
                    "applied",
                    "Unrelated tracker notes",
                    "2026-09-03T00:00:00+00:00",
                    "",
                ),
            ],
            job,
        )

        self.assertEqual(result, 0)
        self.assertEqual(
            set(saved),
            {"sqlite-id-b", "unrelated-json"},
        )
        self.assertEqual(saved["sqlite-id-b"]["id"], "sqlite-id-b")
        self.assertEqual(
            saved["sqlite-id-b"]["saved_description"],
            "Saved role description",
        )
        self.assertEqual(
            saved["sqlite-id-b"]["saved_note"],
            "Useful detail from duplicate JSON record",
        )
        self.assertEqual(saved["sqlite-id-b"]["status"], "applied")
        self.assertEqual(
            saved["unrelated-json"]["description"],
            "Unrelated saved listing",
        )

        rows_by_id = {row[0]: row for row in rows}
        self.assertEqual(
            set(rows_by_id),
            {"sqlite-id-b", "sqlite-duplicate", "unrelated-sqlite"},
        )
        self.assertEqual(
            rows_by_id["sqlite-id-b"][1:],
            (
                "Tracked application title",
                "Example",
                "https://EXAMPLE.com/job/",
                "interview",
                "Keep tracker notes",
                "2026-11-20",
            ),
        )
        self.assertEqual(
            rows_by_id["sqlite-duplicate"][4:],
            ("offer", "Do not delete this existing record", "2026-11-21"),
        )
        self.assertEqual(
            rows_by_id["unrelated-sqlite"][4:],
            ("applied", "Unrelated tracker notes", ""),
        )
        self.assertIn("Already present in saved state: 1", output)
        self.assertIn("New listings: 0", output)

    def test_distinct_sqlite_canonical_urls_remain_distinct(self):
        job = greenhouse_job(
            42,
            "Java Developer Intern",
            "https://example.com/new-job",
        )
        result, rows, saved, output = self.run_discovery_with_tracker(
            {},
            [
                (
                    "existing-job-id",
                    "Existing unrelated application",
                    "Example",
                    "https://example.com/other-job",
                    "applied",
                    "Unrelated notes",
                    "2026-09-01T00:00:00+00:00",
                    "",
                )
            ],
            job,
        )

        self.assertEqual(result, 0)
        self.assertEqual({row[0] for row in rows}, {
            "existing-job-id",
            "gh:board:42",
        })
        self.assertEqual(set(saved), {"gh:board:42"})
        self.assertIn("Already present in saved state: 0", output)
        self.assertIn("New listings: 1", output)

    def test_discover_reports_stage_counts_and_source_errors_safely(self):
        greenhouse_payload = {
            "jobs": [
                greenhouse_job(1, "Software Engineering Intern", "https://example.com/new"),
                greenhouse_job(2, "Java Intern", "https://example.com/new?utm_source=board"),
                greenhouse_job(None, "Java Intern", "https://example.com/no-id"),
                greenhouse_job(3, "Web Development Intern", ""),
                greenhouse_job(4, "Sales Representative Intern", "https://example.com/role"),
                greenhouse_job(5, "Java Developer Intern", "https://[invalid/"),
                greenhouse_job(6, "Backend Developer Intern", "https://example.com/existing-id"),
                greenhouse_job(7, "Java Intern", "https://example.com/existing-url"),
                greenhouse_job(8, "Software Development Intern", "https://example.com/new-second"),
            ]
        }
        lever_payload = [
            lever_job("9", "Full Stack Developer Intern", "https://example.com/new")
        ]
        old = {
            "gh:gh-test:6": {
                "id": "gh:gh-test:6",
                "url": "https://example.com/previous-id-url",
                "status": "applied",
            },
            "saved-other-id": {
                "id": "saved-other-id",
                "url": "https://example.com/existing-url?utm_campaign=old",
                "description": "Keep this saved description",
            },
        }
        config = {
            "greenhouse": [
                {"company": "Good Board", "token": "gh-test"},
                {"company": "Unavailable Board", "token": "bad-token"},
            ],
            "lever": [{"company": "Lever Board", "site": "lever-test"}],
            "exclude_keywords": ["senior"],
        }
        good_response = response(greenhouse_payload)
        lever_response = response(lever_payload)
        failed_response = response(None)
        failed_response.raise_for_status.side_effect = requests.HTTPError(
            "https://example.invalid/bad-token",
            response=Mock(status_code=429),
        )

        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            state_path = temp_path / "jobs.json"
            database_path = temp_path / "tracker.db"
            state_path.write_text(json.dumps(old), encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                patch.object(radar, "load_config", return_value=config),
                patch.object(radar, "load_state", return_value=old),
                patch.object(radar, "STATE", state_path),
                patch.object(radar, "DB", database_path),
                patch("src.radar.requests.get", side_effect=[
                    good_response, failed_response, lever_response,
                ]) as mocked_get,
                patch.object(radar, "notify") as mocked_notify,
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                result = radar.discover()

            self.assertEqual(result, 1)
            self.assertEqual(mocked_get.call_count, 3)
            mocked_notify.assert_called_once()
            output = stdout.getvalue()
            errors = stderr.getvalue()
            self.assertIn("Greenhouse Good Board: returned 9 listings", output)
            self.assertIn(
                "Greenhouse Unavailable Board: ERROR - HTTP error (status 429)",
                output,
            )
            self.assertIn("Lever Lever Board: returned 1 listings", output)
            self.assertIn("Listings returned by successful sources: 10", output)
            self.assertIn("Rejected: missing ID: 1", output)
            self.assertIn("Rejected: missing URL: 1", output)
            self.assertIn("Rejected: invalid URL: 1", output)
            self.assertIn("Rejected: role filter: 1", output)
            self.assertIn("Duplicate listings removed: 2", output)
            self.assertIn("Relevant unique listings: 4", output)
            self.assertIn("Already present in saved state: 2", output)
            self.assertIn("New listings: 2", output)
            self.assertIn("HTTP error=1", errors)
            self.assertNotIn("bad-token", output + errors)
            self.assertNotIn("example.invalid", output + errors)
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertIn("gh:gh-test:1", saved)
            self.assertIn("gh:gh-test:8", saved)
            self.assertNotIn("gh:gh-test:7", saved)
            self.assertEqual(len(saved), 4)
            self.assertEqual(saved["gh:gh-test:6"]["status"], "applied")
            self.assertEqual(saved["saved-other-id"]["id"], "saved-other-id")
            connection = sqlite3.connect(database_path)
            try:
                application_ids = {
                    row[0]
                    for row in connection.execute(
                        "SELECT job_id FROM applications"
                    )
                }
            finally:
                connection.close()
            self.assertEqual(
                application_ids,
                {
                    "gh:gh-test:1",
                    "gh:gh-test:6",
                    "saved-other-id",
                    "gh:gh-test:8",
                },
            )

    def test_notification_errors_are_reported_without_sensitive_details(self):
        token = "123456:FAKE-TELEGRAM-TOKEN"
        secret_url = f"https://api.telegram.org/bot{token}/sendMessage"
        telegram_error = requests.HTTPError(
            f"401 Unauthorized for url: {secret_url}",
            response=Mock(status_code=401),
        )
        email_secret = "smtp-password=FAKE-SMTP-PASSWORD"
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch.object(
                radar,
                "send_email",
                side_effect=RuntimeError(email_secret),
            ),
            patch.object(radar, "send_telegram", side_effect=telegram_error),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            result = radar.notify("Subject", "Body")

        self.assertFalse(result)
        diagnostics = stdout.getvalue() + stderr.getvalue()
        self.assertIn("Email delivery failed: error (RuntimeError)", diagnostics)
        self.assertIn(
            "Telegram delivery failed: HTTP error (status 401)",
            diagnostics,
        )
        for secret in (token, secret_url, email_secret, "FAKE-SMTP-PASSWORD"):
            self.assertNotIn(secret, diagnostics)

    def test_source_failure_diagnostics_include_source_and_error_type(self):
        config = {
            "greenhouse": [
                {"company": "Slow Board", "token": "secret-token"},
                {"company": "Invalid JSON Board", "token": "json"},
                {"company": "Invalid Payload Board", "token": "payload"},
                {"company": "HTTP Board", "token": "http"},
            ],
            "lever": [{"company": "Broken Board", "site": "lever"}],
        }
        invalid_json_response = response(None)
        invalid_json_response.json.side_effect = json.JSONDecodeError(
            "sensitive response body", "", 0
        )
        invalid_payload_response = response({"unexpected": []})
        http_error_response = response(None)
        http_error_response.raise_for_status.side_effect = requests.HTTPError(
            "sensitive endpoint",
            response=Mock(status_code=502),
        )
        with tempfile.TemporaryDirectory() as temp:
            state_path = Path(temp) / "jobs.json"
            database_path = Path(temp) / "tracker.db"
            output = io.StringIO()
            errors = io.StringIO()
            with (
                patch.object(radar, "load_config", return_value=config),
                patch.object(radar, "load_state", return_value={}),
                patch.object(radar, "STATE", state_path),
                patch.object(radar, "DB", database_path),
                patch(
                    "src.radar.requests.get",
                    side_effect=[
                        requests.Timeout("secret-token endpoint"),
                        invalid_json_response,
                        invalid_payload_response,
                        http_error_response,
                        requests.ConnectionError("sensitive endpoint"),
                    ],
                ),
                patch.object(radar, "notify"),
                contextlib.redirect_stdout(output),
                contextlib.redirect_stderr(errors),
            ):
                result = radar.discover()

            self.assertEqual(result, 1)
            self.assertIn("Greenhouse Slow Board: ERROR - timeout", output.getvalue())
            self.assertIn(
                "Greenhouse Invalid JSON Board: ERROR - JSON parsing error",
                output.getvalue(),
            )
            self.assertIn(
                "Greenhouse Invalid Payload Board: ERROR - "
                "invalid payload (expected an object containing a jobs list)",
                output.getvalue(),
            )
            self.assertIn(
                "Greenhouse HTTP Board: ERROR - HTTP error (status 502)",
                output.getvalue(),
            )
            self.assertIn(
                "Lever Broken Board: ERROR - request error (ConnectionError)",
                output.getvalue(),
            )
            self.assertIn("JSON parsing error=1", errors.getvalue())
            self.assertIn("invalid payload=1", errors.getvalue())
            self.assertIn("HTTP error=1", errors.getvalue())
            self.assertIn("timeout=1", errors.getvalue())
            self.assertIn("request error=1", errors.getvalue())
            self.assertNotIn("secret-token", output.getvalue() + errors.getvalue())
            self.assertNotIn("sensitive endpoint", output.getvalue() + errors.getvalue())


if __name__ == "__main__":
    unittest.main()
