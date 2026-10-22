

import argparse

import html

import json

import os

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
import re

import smtplib
import ssl
import sqlite3

import sys

from datetime import date, datetime, timedelta, timezone

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


def validate_deadline(value):
    """Validate a date-only deadline in YYYY-MM-DD format."""
    if not value:
        return ""
    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(
            "Deadline must be a real date in YYYY-MM-DD format, e.g. 2026-11-13."
        ) from exc



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



    # Create the applications table if it does not exist.

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



    # Safely migrate existing databases to support deadlines.

    columns = {

        row["name"]

        for row in conn.execute(

            "PRAGMA table_info(applications)"

        ).fetchall()

    }



    if "deadline" not in columns:

        conn.execute(

            "ALTER TABLE applications "

            "ADD COLUMN deadline TEXT DEFAULT ''"

        )



    # Create the contacts table if it does not exist.

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



    title_text = normalize(job.get("title", ""))

    excluded_title_terms = [
        "principal", "senior", "staff engineer", "director",
        "vice president", "chief ", "distinguished", "head of",
        "lead software engineer", "lead developer", "lead engineer",
        "sales", "marketing", "recruiter", "recruiting",
        "human resources", "people systems", "partner manager",
        "account executive", "customer success", "business development",
        "product management",
    ]

    if any(term in title_text for term in excluded_title_terms):
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







def calculate_priority(job, eligibility):
    """Conservative ranking for a 2027 Indian B.Tech CSE/AI student."""
    score = 0
    location_fit = eligibility.get("location_fit", "unknown")
    graduation = eligibility.get("graduation_year", "unknown")
    work_auth = eligibility.get("work_authorization", "unknown")
    experience = eligibility.get("experience_fit", "unknown")
    title = normalize(job.get("title", ""))

    if location_fit == "india_or_remote_india":
        score += 4
    elif location_fit == "remote_scope_unknown":
        score += 2
    elif location_fit == "outside_india_or_unclear":
        score += 1

    if graduation == "mentions_2028":
        score += 3
    elif graduation == "possible_mismatch":
        score -= 4

    if experience == "early_career_signal":
        score += 3
    elif experience == "experience_requirement_mentioned":
        score -= 2

    if work_auth == "restriction_mentioned":
        score -= 3
    elif work_auth == "sponsorship_mentioned":
        score += 1

    if any(term in title for term in (
        "software", "backend", "developer", "engineering",
        "machine learning", "artificial intelligence",
        "data engineer", "devops", "security engineer",
        "platform engineer", "research engineer",
    )):
        score += 2

    return max(0, min(10, score))


def priority_label(job):
    eligibility = job.get("eligibility") or {}
    graduation = eligibility.get("graduation_year", "unknown")
    location = eligibility.get("location_fit", "unknown")
    work = eligibility.get("work_authorization", "unknown")
    experience = eligibility.get("experience_fit", "unknown")

    if graduation == "possible_mismatch":
        return "LIKELY MISMATCH - CHECK REQUIREMENTS"

    if work == "restriction_mentioned":
        return "CHECK WORK AUTHORIZATION"

    if graduation == "mentions_2028" and location == "india_or_remote_india":
        return "HIGH PRIORITY - VERIFY OFFICIAL CRITERIA"

    if location == "india_or_remote_india" and experience == "early_career_signal":
        return "PRIORITIZE - VERIFY GRADUATION CRITERIA"

    if experience == "early_career_signal" and location == "outside_india_or_unclear":
        return "OVERSEAS EARLY-CAREER - CHECK VISA AND LOCATION"

    if experience == "early_career_signal":
        return "EARLY-CAREER - CHECK REQUIREMENTS"

    return "CHECK REQUIREMENTS"


def assess_eligibility(job):
    """Conservatively screen a listing for a 2028 Indian B.Tech student."""
    title = normalize(job.get("title", ""))
    location = normalize(job.get("location", ""))
    description = normalize(job.get("description", ""))
    text = f"{title} {location} {description}"
    title_text = title

    result = {
        "status": "needs_review",
        "graduation_year": "unknown",
        "location_fit": "unknown",
        "work_authorization": "unknown",
        "experience_fit": "unknown",
        "reasons": [],
    }

    if not title or not job.get("url"):
        result["reasons"].append("Missing title or official listing URL.")
        return result

    # Match explicit year signals in titles, and graduation-specific
    # wording in descriptions to avoid unrelated dates.
    title_year_2028 = re.search(
        r"\b2028\b|\b(?:summer|winter|fall|spring)\s+['']?28\b",
        title,
        re.IGNORECASE,
    )

    graduation_2028 = re.search(
        r"\bclass of\s*['']?2028\b|"
        r"\b(?:graduat(?:e|es|ed|ing|ion)|class)\b.{0,60}\b2028\b|"
        r"\b2028\b.{0,60}\b(?:graduat(?:e|es|ed|ing|ion)|class)\b",
        description,
        re.IGNORECASE,
    )

    conflicting_graduation = re.search(
        r"\bclass of\s*['']?(2025|2026|2027|2029)\b|"
        r"\b(?:graduat(?:e|es|ed|ing|ion)|class)\b.{0,60}\b(2025|2026|2027|2029)\b|"
        r"\b(2025|2026|2027|2029)\b.{0,60}\b(?:graduat(?:e|es|ed|ing|ion)|class)\b",
        description,
        re.IGNORECASE,
    )

    if title_year_2028 or graduation_2028:
        result["graduation_year"] = "mentions_2028"
        result["reasons"].append(
            "A 2028 graduation signal appears in the title or criteria; "
            "confirm the official eligibility requirements."
        )
    elif conflicting_graduation:
        result["graduation_year"] = "possible_mismatch"
        result["reasons"].append(
            "The listing may target a different graduation year; "
            "check the official eligibility requirements."
        )

    if result["graduation_year"] == "unknown":
        result["reasons"].append(
            "No clear graduation-year requirement found."
        )

    # Use both the location field and title for geographic clues.
    location_evidence = f"{location} {title}".casefold()

    india_terms = (
        "india", "bengaluru", "bangalore", "hyderabad", "mumbai",
        "delhi", "pune", "noida", "gurugram", "gurgaon", "chennai",
        "kanpur", "kolkata", "indore", "jaipur", "ahmedabad",
    )
    overseas_terms = (
        "austin", "san francisco", "new york", "seattle", "toronto",
        "dublin", "london", "singapore", "bucharest", "barcelona",
        "dallas", "boston", "chicago", "los angeles", "denver",
        "united states", "usa", "canada", "ireland", "united kingdom",
        "romania", "spain",
    )

    if any(term in location_evidence for term in india_terms):
        result["location_fit"] = "india_or_remote_india"
    elif any(term in location_evidence for term in overseas_terms):
        result["location_fit"] = "outside_india_or_unclear"
    elif location.casefold() in {
        "in-office", "in office", "onsite", "on-site", "hybrid",
        "remote", "remote worldwide",
    }:
        result["location_fit"] = "location_unknown"
    elif "remote" in location.casefold():
        result["location_fit"] = "remote_scope_unknown"
    elif location:
        result["location_fit"] = "outside_india_or_unclear"
    else:
        result["location_fit"] = "location_unknown"

    result["reasons"].append(
        "Confirm the work location and whether applicants residing in India "
        "are accepted."
    )

    if re.search(
        r"\b(no visa sponsorship|unable to sponsor|must be authorized to work|"
        r"must have the right to work|without sponsorship|"
        r"will not sponsor|cannot sponsor)\b",
        text,
    ):
        result["work_authorization"] = "restriction_mentioned"
        result["reasons"].append(
            "A work-authorization restriction is mentioned; compare it "
            "with your circumstances."
        )
    elif re.search(
        r"\b(visa sponsorship available|will sponsor visas|"
        r"sponsorship provided|visa sponsorship)\b",
        text,
    ):
        result["work_authorization"] = "sponsorship_mentioned"
        result["reasons"].append(
            "Sponsorship is mentioned; confirm eligibility and conditions."
        )
    else:
        result["reasons"].append(
            "Work authorization or visa sponsorship is not clearly established."
        )

    # Role titles can indicate early-career work, but not guarantee eligibility.
    if re.search(r"\b\d+\+?\s+years? of experience\b", text):
        result["experience_fit"] = "experience_requirement_mentioned"
        result["reasons"].append(
            "The listing mentions years of experience; review the exact requirement."
        )
    elif re.search(
        r"\b(intern|internship|new grad|new graduate|early career|"
        r"university graduate|graduate program|graduate role|"
        r"entry level|entry-level|students welcome|currently enrolled|"
        r"no experience required|without prior experience)\b",
        title_text + " " + description,
    ):
        result["experience_fit"] = "early_career_signal"
        result["reasons"].append(
            "The title or listing suggests an early-career role; "
            "confirm degree, experience, and enrollment requirements."
        )
    else:
        result["reasons"].append("Experience requirements need review.")

    # Screening is not official verification.
    if result["graduation_year"] == "possible_mismatch":
        result["status"] = "likely_ineligible"
        result["reasons"].append(
            "The stated graduation year may conflict with your 2028 batch; "
            "verify the official eligibility criteria."
        )
    else:
        result["status"] = "needs_review"

    return result


def clean(job):

    """Keep saved job records compact."""

    fields = (

        "id",

        "company",

        "title",

        "location",

        "url",

        "description",

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

    with smtplib.SMTP(host, 587, timeout=25) as server:
        server.ehlo()
        server.starttls(context=ssl.create_default_context())
        server.ehlo()
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
    return bool(sent)


# DISCOVERY

# ==================================================





def canonical_url(value):

    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit



    parts = urlsplit(str(value or "").strip())

    if not parts.scheme or not parts.hostname:

        return ""



    tracking = {

        "utm_source", "utm_medium", "utm_campaign",

        "utm_term", "utm_content", "gclid", "fbclid",

    }

    query = urlencode([

        (key, val)

        for key, val in parse_qsl(parts.query, keep_blank_values=True)

        if key.lower() not in tracking

    ])

    return urlunsplit((

        parts.scheme.lower(),

        parts.netloc.lower(),

        parts.path.rstrip("/") or "/",

        query,

        "",

    ))





def deduplicate_jobs(jobs):

    unique = []

    seen_ids = set()

    seen_urls = set()



    for job in jobs:

        job_id = str(job.get("id", "")).strip()

        url_key = canonical_url(job.get("url", ""))



        if not job_id or not url_key:

            continue

        if job_id in seen_ids or url_key in seen_urls:

            continue



        seen_ids.add(job_id)

        seen_urls.add(url_key)

        unique.append(job)



    return unique





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

        job for job in found

        if job.get("url") and job.get("id") and is_relevant(job, cfg)

    ]

    relevant = deduplicate_jobs(relevant)



    current = {

        job["id"]: {

            **clean(job),

            "eligibility": assess_eligibility(job),
            "priority_score": calculate_priority(
                job, assess_eligibility(job)
            ),

        }

        for job in relevant

    }



    old_urls = {

        canonical_url(job.get("url", ""))

        for job in old.values()

        if isinstance(job, dict) and job.get("url")

    }



    new_jobs = [

        job for job in relevant

        if job["id"] not in old

        and canonical_url(job["url"]) not in old_urls

    ]



    # Merge fresh listings with saved jobs so missing listings are retained.
    merged = dict(old)
    for job_id, fresh_job in current.items():
        previous = merged.get(job_id)
        if isinstance(previous, dict):
            merged[job_id] = {**previous, **fresh_job}
        else:
            merged[job_id] = fresh_job

    STATE.write_text(
        json.dumps(merged, indent=2, ensure_ascii=False),
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




def refresh_priority_scores():
    """Refresh scores for saved jobs without fetching or notifying."""
    state = load_state()
    if not state:
        print("No saved jobs to refresh.")
        return

    backup = STATE.with_name("jobs.json.before-priority-refresh")
    if not backup.exists():
        backup.write_text(STATE.read_text(encoding="utf-8"), encoding="utf-8")

    count = 0
    for job in state.values():
        if not isinstance(job, dict):
            continue
        eligibility = assess_eligibility(job)
        job["eligibility"] = eligibility
        job["priority_score"] = calculate_priority(job, eligibility)
        count += 1

    STATE.write_text(
        json.dumps(state, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Refreshed priority scores for {count} saved jobs.")
    print(f"Backup: {backup.name}")


def list_jobs(args=None):

    state = load_state()
    jobs = list(state.values())

    if args and getattr(args, "relevant_only", False):
        cfg = load_config()
        jobs = [job for job in jobs if is_relevant(job, cfg)]

    if args and getattr(args, "internships", False):
        jobs = [
            job for job in jobs
            if any(term in normalize(job.get("title", "")) for term in (
                "intern", "internship", "co-op", "coop"
            ))
        ]

    if args and getattr(args, "new_grad", False):
        jobs = [
            job for job in jobs
            if any(term in normalize(job.get("title", "")) for term in (
                "new grad", "new graduate", "graduate software",
                "graduate engineer", "early career"
            ))
        ]

    jobs = sorted(
        jobs,
        key=lambda job: (
            -int(job.get("priority_score", 0) or 0),
            job.get("company", "").lower(),
            job.get("title", "").lower(),
        ),
    )



    for job in jobs:

        eligibility = job.get("eligibility") or {}

        print(

            f"{job.get('id', '')} | "

            f"{job.get('company', '')} | "

            f"{job.get('title', '')} | "

            f"{job.get('location', '')} | "

            f"{job.get('url', '')} | "

            f"eligibility: {eligibility.get('status', 'needs_review')} | "

            f"graduation: {eligibility.get('graduation_year', 'unknown')} | "

            f"location fit: {eligibility.get('location_fit', 'unknown')} | "

            f"work authorization: {eligibility.get('work_authorization', 'unknown')} | "

            f"experience: {eligibility.get('experience_fit', 'unknown')} | "
            f"priority score: {job.get('priority_score', 0)}/10 | "
            f"screen: {priority_label(job)}"

        )



    if not jobs:

        print("No saved jobs. Run: python -m src.radar discover")



def add_application(args):

    conn = db_conn()

    conn.execute(
        """
        INSERT INTO applications
            (job_id, title, company, url, status, notes, updated_at, deadline)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(job_id) DO UPDATE SET
            status = excluded.status,
            notes = excluded.notes,
            updated_at = excluded.updated_at,
            deadline = CASE
                WHEN excluded.deadline != '' THEN excluded.deadline
                ELSE applications.deadline
            END
        """,
        (
            args.job_id,
            args.title or args.job_id,
            args.company or "Unknown",
            args.url or "",
            args.status,
            args.notes or "",
            now_iso(),
            args.deadline or "",
        ),
    )

    conn.commit()
    conn.close()

    deadline_text = f" (deadline: {args.deadline})" if args.deadline else ""
    print(f"Saved {args.job_id} as {args.status}{deadline_text}.")


def applications():

    conn = db_conn()

    rows = conn.execute(
        """
        SELECT job_id, title, company, status, url, updated_at, deadline
        FROM applications
        ORDER BY CASE WHEN deadline = '' THEN 1 ELSE 0 END, deadline, updated_at DESC
        """
    ).fetchall()

    conn.close()

    for row in rows:
        deadline = row["deadline"] or "No deadline set"
        print(
            f"{row['status']:12} | {row['company']} | "
            f"{row['title']} | deadline: {deadline} | "
            f"{row['job_id']} | {row['url']}"
        )


def deadlines():
    """List overdue applications and deadlines due within two calendar days.

    Deadlines are date-only (YYYY-MM-DD), so this is a calendar-day approximation
    of a 48-hour alert, not an exact time-of-day calculation.
    """
    today = date.today()
    cutoff = today + timedelta(days=2)
    conn = db_conn()
    rows = conn.execute(
        """
        SELECT job_id, title, company, status, url, deadline
        FROM applications
        WHERE COALESCE(deadline, '') != ''
          AND status NOT IN ('rejected', 'offer', 'closed')
        ORDER BY deadline ASC
        """
    ).fetchall()
    conn.close()

    overdue = []
    due_soon = []
    for row in rows:
        try:
            due_date = date.fromisoformat(row["deadline"])
        except (TypeError, ValueError):
            print(
                f"Skipping invalid deadline for {row['job_id']}: {row['deadline']}",
                file=sys.stderr,
            )
            continue
        if due_date < today:
            overdue.append(row)
        elif due_date <= cutoff:
            due_soon.append(row)

    if not overdue and not due_soon:
        print("No active application deadlines are overdue or due within the next two calendar days.")
        return

    if overdue:
        print("OVERDUE:")
        for row in overdue:
            print(
                f"  {row['deadline']} | {row['company']} | {row['title']} | "
                f"{row['job_id']} | {row['url']}"
            )
    if due_soon:
        print("DUE WITHIN TWO CALENDAR DAYS:")
        for row in due_soon:
            days_left = (date.fromisoformat(row["deadline"]) - today).days
            timing = "today" if days_left == 0 else "tomorrow" if days_left == 1 else "within 2 days"
            print(
                f"  {row['deadline']} ({timing}) | {row['company']} | "
                f"{row['title']} | {row['job_id']} | {row['url']}"
            )


# ==================================================



def remind_deadlines():
    """Send one reminder per active application deadline."""
    from datetime import date, timedelta

    today = date.today()
    limit = today + timedelta(days=2)
    conn = db_conn()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS deadline_reminders (
            job_id TEXT NOT NULL,
            deadline TEXT NOT NULL,
            sent_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (job_id, deadline)
        )
    """)
    conn.commit()

    rows = conn.execute("""
        SELECT job_id, title, company, url, status, deadline
        FROM applications
        WHERE TRIM(COALESCE(deadline, '')) <> ''
          AND status NOT IN ('rejected', 'offer', 'closed')
        ORDER BY deadline
    """).fetchall()

    count = 0
    for row in rows:
        try:
            due = date.fromisoformat(row["deadline"])
        except (ValueError, TypeError):
            print(f"Skipping invalid deadline: {row['job_id']}")
            continue

        if due > limit:
            continue

        existing = conn.execute(
            "SELECT 1 FROM deadline_reminders WHERE job_id=? AND deadline=?",
            (row["job_id"], row["deadline"]),
        ).fetchone()
        if existing:
            continue

        if due < today:
            timing = f"OVERDUE by {(today - due).days} day(s)"
        elif due == today:
            timing = "DUE TODAY"
        else:
            timing = f"DUE IN {(due - today).days} day(s)"

        subject = f"Application deadline: {row['company']} - {row['title']}"
        body = (
            f"{timing}\n"
            f"Deadline: {row['deadline']}\n"
            f"Company: {row['company']}\n"
            f"Role: {row['title']}\n"
            f"Status: {row['status']}\n"
            f"Application ID: {row['job_id']}\n"
            f"Link: {row['url'] or 'Not provided'}"
        )

        if notify(subject, body):
            conn.execute(
                "INSERT OR IGNORE INTO deadline_reminders (job_id, deadline) VALUES (?, ?)",
                (row["job_id"], row["deadline"]),
            )
            conn.commit()
            count += 1

    conn.close()
    print(f"Deadline reminders successfully sent: {count}")
    return 0

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

    list_parser = sub.add_parser("list")
    list_parser.add_argument(
        "--relevant-only",
        action="store_true",
        help="Show only jobs matching the current relevance filter",
    )
    list_parser.add_argument(
        "--internships",
        action="store_true",
        help="Show internship opportunities only",
    )
    list_parser.add_argument(
        "--new-grad",
        action="store_true",
        help="Show graduate and new-grad opportunities only",
    )

    sub.add_parser("applications")
    sub.add_parser("deadlines", help="Show overdue deadlines and those due within two calendar days")
    sub.add_parser("remind-deadlines", help="Send deadline alerts by email or Telegram")

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
    application_parser.add_argument(
        "--deadline",
        type=validate_deadline,
        help="Application deadline in YYYY-MM-DD format",
    )



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



    sub.add_parser("refresh-scores")

    args = parser.parse_args()



    commands = {

        "discover": discover,

        "list": lambda: list_jobs(args),
        "refresh-scores": refresh_priority_scores,

        "applications": applications,
        "deadlines": deadlines,
        "remind-deadlines": remind_deadlines,

        "contacts": contacts,

        "add-application": lambda: add_application(args),

        "add-contact": lambda: add_contact(args),

    }



    result = commands[args.command]()

    if isinstance(result, int):

        raise SystemExit(result)





if __name__ == "__main__":

    main()


