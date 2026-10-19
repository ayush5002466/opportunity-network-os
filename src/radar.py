
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


# ==================================================
# PROJECT PATHS AND SETTINGS
# ==================================================

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CONFIG = ROOT / "config" / "boards.json"
STATE = DATA / "jobs.json"
DB = DATA / "tracker.db"

DATA.mkdir(parents=True, exist_ok=True)

REQUEST_TIMEOUT = 25


# ==================================================
# UTILITIES
# ==================================================

def now_iso():
    return datetime.now(timezone.utc).isoformat()


def normalize(value):
    """Normalize text for reliable comparisons."""
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def strip_html(value):
    """Convert HTML descriptions into readable plain text."""
    value = re.sub(r"<[^>]+>", " ", str(value or ""))
    return normalize(html.unescape(value))


# ==================================================
# DATABASE
# ==================================================

def db_conn():
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


# ==================================================
# CONFIGURATION AND SAVED JOBS
# ==================================================

def load_config():
    if not CONFIG.exists():
        raise SystemExit(
            "Missing config/boards.json. "
            "Copy config/boards.example.json to config/boards.json first."
        )

    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid config/boards.json: {exc}") from exc

    if not isinstance(cfg, dict):
        raise SystemExit("config/boards.json must contain a JSON object.")

    return cfg


def load_state():
    if not STATE.exists():
        return {}

    try:
        state = json.loads(STATE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}

    # Migrate an older list-based jobs.json into a dictionary.
    if isinstance(state, list):
        return {
            job["id"]: job
            for job in state
            if isinstance(job, dict) and job.get("id")
        }

    return state if isinstance(state, dict) else {}


# ==================================================
# JOB SOURCES
# ==================================================

def fetch_greenhouse(company, token):
    url = (
        f"https://boards-api.greenhouse.io/v1/boards/"
        f"{token}/jobs?content=true"
    )

    response = requests.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()

    result = []

    for item in response.json().get("jobs", []):
        result.append({
            "id": f"gh:{token}:{item.get('id')}",
            "company": company,
            "title": item.get("title", ""),
            "location": (
                (item.get("location") or {}).get("name", "")
            ),
            "url": item.get("absolute_url", ""),
            "updated_at": item.get("updated_at", ""),
            "description": strip_html(item.get("content", "")),
            "source": "Greenhouse",
        })

    return result


def fetch_lever(company, site):
    url = f"https://api.lever.co/v0/postings/{site}?mode=json"

    response = requests.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()

    result = []

    for item in response.json():
        categories = item.get("categories") or {}

        description = " ".join([
            item.get("descriptionPlain", "") or "",
            item.get("additional", "") or "",
        ])

        result.append({
            "id": f"lever:{site}:{item.get('id')}",
            "company": company,
            "title": item.get("text", ""),
            "location": categories.get("location", ""),
            "url": item.get("hostedUrl", ""),
            "updated_at": item.get("createdAt", ""),
            "description": strip_html(description),
            "source": "Lever",
        })

    return result


# ==================================================
# EARLY-CAREER RELEVANCE FILTER
# ==================================================

def is_relevant(job, cfg):
    """Filter for technical early-career opportunities."""

    title = normalize(job.get("title", ""))
    if not title:
        return False

    def has_phrase(phrase):
        phrase = normalize(phrase)
        return bool(
            phrase and re.search(
                rf"(?<!\w){re.escape(phrase)}(?!\w)", title
            )
        )

    excluded_titles = [
        "senior", "staff", "principal", "director", "manager",
        "lead", "head", "vice president", "recruiter",
        "account executive", "sales development",
        "business development", "product marketing",
        "operations associate", "solutions architect",
        "strategy and operations", "phd", "ph.d",
        "doctoral", "postdoctoral", "high school",
    ]

    configured_excludes = [
        term for term in cfg.get("exclude_keywords", [])
        if normalize(term)
    ]

    if any(
        has_phrase(term)
        for term in excluded_titles + configured_excludes
    ):
        return False

    early_signals = [
        "intern", "internship", "new grad", "new graduate",
        "graduate", "early career", "early careers",
        "entry level", "entry-level", "junior",
        "apprentice", "apprenticeship", "trainee",
        "fellowship", "fellow", "research assistant",
    ]

    technical_signals = [
        "software", "developer", "engineer", "engineering",
        "backend", "back-end", "frontend", "front-end",
        "full stack", "full-stack", "data", "machine learning",
        "artificial intelligence", "security", "devops",
        "platform", "cloud", "research", "technical",
        "technology", "cyber", "robotics", "systems",
    ]

    has_early_signal = any(
        has_phrase(signal) for signal in early_signals
    )
    has_technical_signal = (
        has_phrase("ai")
        or any(has_phrase(signal) for signal in technical_signals)
    )

    return has_early_signal and has_technical_signal


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


# ==================================================
# NOTIFICATIONS
# ==================================================

def send_telegram(message):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        return False

    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": message[:3900],
            "disable_web_page_preview": True,
        },
        timeout=20,
    )

    response.raise_for_status()
    return True


def send_email(subject, body):
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

    with smtplib.SMTP(host, port, timeout=25) as server:
        server.starttls()
        server.login(user, password)
        server.send_message(message)

    return True


def notify(subject, body):
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

    result = ", ".join(sent) if sent else "none (configure credentials)"
    print(f"Notifications sent: {result}")


# ==================================================
# DISCOVERY
# ==================================================

def discover():
    cfg = load_config()
    old = load_state()

    found = []
    errors = []

    for item in cfg.get("greenhouse", []):
        try:
            found.extend(
                fetch_greenhouse(item["company"], item["token"])
            )
        except Exception as exc:
            errors.append(
                f"Greenhouse {item.get('company', '?')}: {exc}"
            )

    for item in cfg.get("lever", []):
        try:
            found.extend(
                fetch_lever(item["company"], item["site"])
            )
        except Exception as exc:
            errors.append(
                f"Lever {item.get('company', '?')}: {exc}"
            )

    relevant = [
        job
        for job in found
        if job.get("url") and is_relevant(job, cfg)
    ]

    current = {
        job["id"]: clean(job)
        for job in relevant
    }

    new_jobs = [
        job for job in relevant
        if job["id"] not in old
    ]

    STATE.write_text(
        json.dumps(current, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    conn = db_conn()

    for job in relevant:
        conn.execute(
            """
            INSERT OR IGNORE INTO applications
                (job_id, title, company, url, status, notes, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job["id"],
                job["title"],
                job["company"],
                job["url"],
                "discovered",
                "",
                now_iso(),
            ),
        )

    conn.commit()
    conn.close()

    if new_jobs:
        lines = []

        for job in new_jobs[:30]:
            lines.append(
                f"- {job['title']} — {job['company']} "
                f"| {job['location']}\n"
                f"  {job['url']}\n"
                f"  Source: {job['source']}"
            )

        body = (
            f"New potentially relevant public listings: "
            f"{len(new_jobs)}\n\n"
            + "\n\n".join(lines)
            + "\n\nVerify graduation year, work authorization, "
            "experience, deadline, and sponsorship on the official posting."
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
        f"Fetched {len(found)} listings; "
        f"relevant {len(relevant)}; new {len(new_jobs)}."
    )

    return 1 if errors and not found else 0


# ==================================================
# JOB LIST
# ==================================================

def list_jobs():
    state = load_state()

    jobs = sorted(
        state.values(),
        key=lambda job: (
            job.get("company", ""),
            job.get("title", ""),
        ),
    )

    for job in jobs:
        print(
            f"{job.get('id', '')} | "
            f"{job.get('company', '')} | "
            f"{job.get('title', '')} | "
            f"{job.get('location', '')} | "
            f"{job.get('url', '')}"
        )

    if not jobs:
        print("No saved jobs. Run: python -m src.radar discover")


# ==================================================
# APPLICATION TRACKER
# ==================================================

def add_application(args):
    conn = db_conn()

    conn.execute(
        """
        INSERT INTO applications
            (job_id, title, company, url, status, notes, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(job_id) DO UPDATE SET
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

    conn.commit()
    conn.close()

    print(f"Saved {args.job_id} as {args.status}.")


def applications():
    conn = db_conn()

    rows = conn.execute(
        """
        SELECT job_id, title, company, status, url, updated_at
        FROM applications
        ORDER BY updated_at DESC
        """
    ).fetchall()

    conn.close()

    for row in rows:
        print(
            f"{row['status']:12} | {row['company']} | "
            f"{row['title']} | {row['job_id']} | {row['url']}"
        )


# ==================================================
# CONTACT TRACKER
# ==================================================

def add_contact(args):
    conn = db_conn()

    conn.execute(
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

    conn.commit()
    conn.close()

    print(f"Saved contact: {args.name}")


def contacts():
    conn = db_conn()

    rows = conn.execute(
        """
        SELECT name, organization, channel, reason, status, follow_up
        FROM contacts
        ORDER BY id DESC
        """
    ).fetchall()

    conn.close()

    for row in rows:
        print(
            f"{row['status']:14} | {row['name']} | "
            f"{row['organization']} | {row['channel']} | "
            f"reason: {row['reason']} | "
            f"follow-up: {row['follow_up']}"
        )


# ==================================================
# CLI
# ==================================================

def main():
    parser = argparse.ArgumentParser(
        description="Opportunity & Network OS"
    )

    sub = parser.add_subparsers(
        dest="command",
        required=True,
    )

    sub.add_parser("discover")
    sub.add_parser("list")
    sub.add_parser("applications")
    sub.add_parser("contacts")

    application_parser = sub.add_parser("add-application")
    application_parser.add_argument("--job-id", required=True)
    application_parser.add_argument(
        "--status",
        required=True,
        choices=[
            "discovered", "saved", "to_apply", "applied",
            "assessment", "interview", "rejected",
            "offer", "closed",
        ],
    )
    application_parser.add_argument("--title")
    application_parser.add_argument("--company")
    application_parser.add_argument("--url")
    application_parser.add_argument("--notes")

    contact_parser = sub.add_parser("add-contact")
    contact_parser.add_argument("--name", required=True)
    contact_parser.add_argument("--organization")
    contact_parser.add_argument(
        "--channel",
        help="Public profile, community, or event",
    )
    contact_parser.add_argument(
        "--reason",
        help="Specific genuine technical overlap",
    )
    contact_parser.add_argument(
        "--status",
        default="to_reach_out",
        choices=[
            "to_reach_out", "contacted", "replied",
            "follow_up", "connected", "no_response",
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
