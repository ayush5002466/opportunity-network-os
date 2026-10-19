
import argparse
import html
import json
import os
import re
import smtplib
import sqlite3
import sys
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

import requests


# --------------------------------------------------
# PROJECT PATHS
# --------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CONFIG = ROOT / "config" / "boards.json"
STATE = DATA / "jobs.json"
DB = DATA / "tracker.db"

DATA.mkdir(parents=True, exist_ok=True)

REQUEST_TIMEOUT = 25


# --------------------------------------------------
# UTILITIES
# --------------------------------------------------

def now_iso():
    return datetime.now(timezone.utc).isoformat()


def normalize(value):
    """Normalize text for case-insensitive matching."""
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def strip_html(value):
    """Remove HTML tags and decode HTML entities."""
    value = re.sub(r"<[^>]+>", " ", str(value or ""))
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def db_conn():
    """Create the SQLite database and required tables."""
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    conn.execute("""
        CREATE TABLE IF NOT EXISTS applications (
            job_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            company TEXT NOT NULL,
            url TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'discovered',
            notes TEXT DEFAULT '',
            updated_at TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            organization TEXT DEFAULT '',
            channel TEXT DEFAULT '',
            reason TEXT DEFAULT '',
            status TEXT DEFAULT 'to_reach_out',
            follow_up TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )
    """)

    conn.commit()
    return conn


def load_config():
    """Load the job-board and filtering configuration."""
    if not CONFIG.exists():
        raise SystemExit(
            "Missing config/boards.json. "
            "Create it using config/boards.example.json."
        )

    try:
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid config/boards.json: {exc}") from exc

    if not isinstance(config, dict):
        raise SystemExit("config/boards.json must contain a JSON object.")

    return config


def load_state():
    """Load previously discovered jobs."""
    if not STATE.exists():
        return {}

    try:
        state = json.loads(STATE.read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else {}
    except (json.JSONDecodeError, OSError) as exc:
        print(f"Warning: could not load saved jobs: {exc}", file=sys.stderr)
        return {}


def clean(job):
    """Keep saved job records compact."""
    fields = (
        "id",
        "company",
        "title",
        "location",
        "url",
        "updated_at",
        "source",
    )

    return {field: job.get(field, "") for field in fields}


# --------------------------------------------------
# JOB RELEVANCE FILTERING
# --------------------------------------------------

def is_relevant(job, cfg):
    """
    Return True when a listing matches configured target keywords
    and does not match excluded job titles.

    This is a relevance filter, not a guarantee of candidate eligibility.
    """
    title = normalize(job.get("title"))
    description = normalize(job.get("description"))

    if not title:
        return False

    # Reject roles that are not suitable software/technical opportunities
    # for this radar, even if their descriptions mention software.
    excluded_title_phrases = [
        "recruiter",
        "recruiting",
        "talent acquisition",
        "solutions architect",
        "technical program manager",
        "program manager",
        "strategy and operations",
        "operations lead",
        "account executive",
        "sales development",
        "customer success manager",
    ]

    if any(phrase in title for phrase in excluded_title_phrases):
        return False

    # Apply configured exclusions to the title.
    # Examples: senior, staff, principal, director, manager.
    excludes = [
        normalize(item)
        for item in cfg.get("exclude_keywords", [])
        if normalize(item)
    ]

    if any(exclusion in title for exclusion in excludes):
        return False

    # Reject explicit high-experience requirements when found in the
    # listing text. These patterns are intentionally conservative.
    experience_patterns = [
        r"\b(?:7|8|9|10|11|12|15)\s*\+\s*years?\b",
        r"\b(?:seven|eight|nine|ten|fifteen)\s*\+?\s*years?\b",
        r"\bminimum\s+(?:of\s+)?(?:7|8|9|10|11|12|15)\s+years?\b",
    ]

    combined_text = f"{title} {description}"

    if any(
        re.search(pattern, combined_text, flags=re.IGNORECASE)
        for pattern in experience_patterns
    ):
        return False

    # Match configured keywords against both title and description.
    keywords = [
        normalize(item)
        for item in cfg.get("keywords", [])
        if normalize(item)
    ]

    if not keywords:
        return False

    return any(keyword in combined_text for keyword in keywords)


# --------------------------------------------------
# GREENHOUSE INTEGRATION
# --------------------------------------------------

def fetch_greenhouse(company, token):
    """Fetch public job listings from a Greenhouse job board."""
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"

    response = requests.get(
        url,
        params={"content": "true"},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()

    jobs = []

    for item in response.json().get("jobs", []):
        jobs.append({
            "id": f"gh:{token}:{item.get('id')}",
            "company": company,
            "title": item.get("title", ""),
            "location": (item.get("location") or {}).get("name", ""),
            "url": item.get("absolute_url", ""),
            "updated_at": item.get("updated_at", ""),
            "description": strip_html(item.get("content", "")),
            "source": "Greenhouse",
        })

    return jobs


# --------------------------------------------------
# LEVER INTEGRATION
# --------------------------------------------------

def fetch_lever(company, site):
    """Fetch public job listings from a Lever job board."""
    url = f"https://api.lever.co/v0/postings/{site}"

    response = requests.get(
        url,
        params={"mode": "json"},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()

    jobs = []

    for item in response.json():
        categories = item.get("categories") or {}

        description = " ".join([
            item.get("descriptionPlain", "") or "",
            item.get("additionalPlain", "") or item.get("additional", "") or "",
        ])

        jobs.append({
            "id": f"lever:{site}:{item.get('id')}",
            "company": company,
            "title": item.get("text", ""),
            "location": categories.get("location", ""),
            "url": item.get("hostedUrl", ""),
            "updated_at": item.get("createdAt", ""),
            "description": strip_html(description),
            "source": "Lever",
        })

    return jobs


# --------------------------------------------------
# EMAIL NOTIFICATIONS
# --------------------------------------------------

def send_email(subject, body):
    """Send an email if SMTP credentials are configured."""
    host = os.getenv("SMTP_HOST")
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASSWORD")
    sender = os.getenv("ALERT_FROM", user or "")
    recipient = os.getenv("ALERT_TO")

    if not all([host, user, password, sender, recipient]):
        return False

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = recipient
    message.set_content(body)

    port = int(os.getenv("SMTP_PORT", "587"))

    with smtplib.SMTP(host, port, timeout=REQUEST_TIMEOUT) as server:
        server.starttls()
        server.login(user, password)
        server.send_message(message)

    return True


# --------------------------------------------------
# TELEGRAM NOTIFICATIONS
# --------------------------------------------------

def send_telegram(message_text):
    """Send a Telegram message if bot credentials are configured."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    response = requests.post(
        url,
        json={
            "chat_id": chat_id,
            "text": message_text[:3900],
            "disable_web_page_preview": True,
        },
        timeout=20,
    )
    response.raise_for_status()

    return True


# --------------------------------------------------
# NOTIFICATION DISPATCH
# --------------------------------------------------

def notify(subject, body):
    """Attempt configured email and Telegram notifications."""
    sent = []

    try:
        if send_email(subject, body):
            sent.append("email")
    except Exception as exc:
        print(f"Email delivery failed: {exc}", file=sys.stderr)

    try:
        if send_telegram(f"{subject}\n\n{body}"):
            sent.append("telegram")
    except Exception as exc:
        print(f"Telegram delivery failed: {exc}", file=sys.stderr)

    if sent:
        print("Notifications sent: " + ", ".join(sent))
    else:
        print("Notifications not sent; configure email or Telegram credentials.")


# --------------------------------------------------
# DISCOVERY
# --------------------------------------------------

def discover():
    """Fetch, filter, save and report relevant job listings."""
    config = load_config()
    old_jobs = load_state()

    fetched_jobs = []
    errors = []

    # Fetch Greenhouse boards.
    for board in config.get("greenhouse", []):
        company = board.get("company", "?")
        token = board.get("token")

        if not token:
            errors.append(f"Greenhouse {company}: missing board token")
            continue

        try:
            fetched_jobs.extend(fetch_greenhouse(company, token))
        except Exception as exc:
            errors.append(f"Greenhouse {company}: {exc}")

    # Fetch Lever boards.
    for board in config.get("lever", []):
        company = board.get("company", "?")
        site = board.get("site")

        if not site:
            errors.append(f"Lever {company}: missing site identifier")
            continue

        try:
            fetched_jobs.extend(fetch_lever(company, site))
        except Exception as exc:
            errors.append(f"Lever {company}: {exc}")

    relevant_jobs = [
        job
        for job in fetched_jobs
        if job.get("id")
        and job.get("url")
        and is_relevant(job, config)
    ]

    # Remove duplicate IDs while preserving the latest encountered record.
    unique_jobs = {
        job["id"]: job
        for job in relevant_jobs
    }
    relevant_jobs = list(unique_jobs.values())

    current_state = {
        job["id"]: clean(job)
        for job in relevant_jobs
    }

    new_jobs = [
        job
        for job in relevant_jobs
        if job["id"] not in old_jobs
    ]

    # Save the latest relevant listings.
    STATE.write_text(
        json.dumps(current_state, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # Preserve application tracking in SQLite.
    connection = db_conn()

    try:
        for job in relevant_jobs:
            connection.execute(
                """
                INSERT OR IGNORE INTO applications
                (job_id, title, company, url, status, notes, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job["id"],
                    job["title"] or "Untitled",
                    job["company"] or "Unknown",
                    job["url"],
                    "discovered",
                    "",
                    now_iso(),
                ),
            )

        connection.commit()
    finally:
        connection.close()

    # Notify about newly discovered listings.
    if new_jobs:
        lines = []

        for job in new_jobs[:30]:
            lines.append(
                f"- {job['title']} — {job['company']}\n"
                f"  Location: {job['location']}\n"
                f"  URL: {job['url']}\n"
                f"  Source: {job['source']}"
            )

        body = (
            f"New potentially relevant public listings: {len(new_jobs)}\n\n"
            + "\n\n".join(lines)
            + "\n\nVerify graduation year, location, work authorization, "
              "experience requirements, sponsorship and deadline on the "
              "official job posting."
        )

        notify(
            f"Job Radar: {len(new_jobs)} new listing(s)",
            body,
        )
    else:
        print("No new relevant listings.")

    if errors:
        print(
            "\nSource errors:\n" + "\n".join(errors),
            file=sys.stderr,
        )

    print(
        f"Fetched {len(fetched_jobs)} listings; "
        f"relevant {len(relevant_jobs)}; "
        f"new {len(new_jobs)}."
    )

    # Return failure only when every source failed or yielded no listings
    # and at least one source error occurred.
    if errors and not fetched_jobs:
        return 1

    return 0


# --------------------------------------------------
# LIST DISCOVERED JOBS
# --------------------------------------------------

def list_jobs():
    """Print the currently saved job listings."""
    state = load_state()

    if not state:
        print("No saved jobs. Run: python -m src.radar discover")
        return

    for job in sorted(
        state.values(),
        key=lambda item: (
            item.get("company", ""),
            item.get("title", ""),
        ),
    ):
        print(
            f"{job.get('id', '')} | "
            f"{job.get('company', '')} | "
            f"{job.get('title', '')} | "
            f"{job.get('location', '')} | "
            f"{job.get('url', '')}"
        )


# --------------------------------------------------
# APPLICATION TRACKER
# --------------------------------------------------

def add_application(args):
    """Add or update an application's status."""
    connection = db_conn()

    try:
        connection.execute(
            """
            INSERT INTO applications
            (job_id, title, company, url, status, notes, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                title = excluded.title,
                company = excluded.company,
                url = excluded.url,
                status = excluded.status,
                notes = excluded.notes,
                updated_at = excluded.updated_at
            """,
            (
                args.job_id,
                args.title or args.job_id,
                args.company or "Unknown",
                args.url or "",
                args.status,
                args.notes or "",
                now_iso(),
            ),
        )

        connection.commit()
    finally:
        connection.close()

    print(f"Saved {args.job_id} as {args.status}.")


def applications():
    """List tracked applications."""
    connection = db_conn()

    try:
        rows = connection.execute(
            """
            SELECT job_id, title, company, status, url, updated_at
            FROM applications
            ORDER BY updated_at DESC
            """
        ).fetchall()
    finally:
        connection.close()

    if not rows:
        print("No applications tracked yet.")
        return

    for row in rows:
        print(
            f"{row['status']:12} | "
            f"{row['company']} | "
            f"{row['title']} | "
            f"{row['job_id']} | "
            f"{row['url']}"
        )


# --------------------------------------------------
# CONTACT TRACKER
# --------------------------------------------------

def add_contact(args):
    """Save a networking contact."""
    connection = db_conn()

    try:
        connection.execute(
            """
            INSERT INTO contacts
            (name, organization, channel, reason, status, follow_up, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                args.name,
                args.organization or "",
                args.channel or "",
                args.reason or "",
                args.status,
                args.follow_up or "",
                now_iso(),
            ),
        )

        connection.commit()
    finally:
        connection.close()

    print(f"Saved contact: {args.name}")


def contacts():
    """List saved networking contacts."""
    connection = db_conn()

    try:
        rows = connection.execute(
            """
            SELECT name, organization, channel, reason, status, follow_up
            FROM contacts
            ORDER BY id DESC
            """
        ).fetchall()
    finally:
        connection.close()

    if not rows:
        print("No contacts tracked yet.")
        return

    for row in rows:
        print(
            f"{row['status']:14} | "
            f"{row['name']} | "
            f"{row['organization']} | "
            f"{row['channel']} | "
            f"reason: {row['reason']} | "
            f"follow-up: {row['follow_up']}"
        )


# --------------------------------------------------
# COMMAND-LINE INTERFACE
# --------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Opportunity Network OS — job discovery and tracking"
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    subparsers.add_parser(
        "discover",
        help="Fetch and filter jobs from configured boards",
    )

    subparsers.add_parser(
        "list",
        help="List saved relevant jobs",
    )

    subparsers.add_parser(
        "applications",
        help="List tracked applications",
    )

    subparsers.add_parser(
        "contacts",
        help="List networking contacts",
    )

    application_parser = subparsers.add_parser(
        "add-application",
        help="Add or update an application",
    )

    application_parser.add_argument("--job-id", required=True)
    application_parser.add_argument(
        "--status",
        required=True,
        choices=[
            "discovered",
            "saved",
            "to_apply",
            "applied",
            "assessment",
            "interview",
            "rejected",
            "offer",
            "closed",
        ],
    )
    application_parser.add_argument("--title")
    application_parser.add_argument("--company")
    application_parser.add_argument("--url")
    application_parser.add_argument("--notes")

    contact_parser = subparsers.add_parser(
        "add-contact",
        help="Add a networking contact",
    )

    contact_parser.add_argument("--name", required=True)
    contact_parser.add_argument("--organization")
    contact_parser.add_argument(
        "--channel",
        help="Public profile, community or event",
    )
    contact_parser.add_argument(
        "--reason",
        help="Specific genuine technical overlap",
    )
    contact_parser.add_argument(
        "--status",
        default="to_reach_out",
        choices=[
            "to_reach_out",
            "contacted",
            "replied",
            "follow_up",
            "connected",
            "no_response",
            "do_not_contact",
        ],
    )
    contact_parser.add_argument("--follow-up")

    args = parser.parse_args()

    commands = {
        "discover": discover,
        "list": list_jobs,
        "applications": applications,
        "contacts": contacts,
        "add-application": lambda: add_application(args),
        "add-contact": lambda: add_contact(args),
    }

    result = commands[args.command]()

    if isinstance(result, int):
        raise SystemExit(result)


if __name__ == "__main__":
    main()
