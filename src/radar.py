
import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "boards.json"
DATA_DIR = ROOT / "data"
JOBS_PATH = DATA_DIR / "jobs.json"

USER_AGENT = "OpportunityNetworkOS/1.0"


def load_config():
    """Load the JSON configuration."""
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"Configuration file not found: {CONFIG_PATH}"
        )

    with CONFIG_PATH.open("r", encoding="utf-8") as file:
        config = json.load(file)

    if not isinstance(config, dict):
        raise ValueError("Configuration must be a JSON object.")

    for key in ("keywords", "exclude_keywords"):
        if not isinstance(config.get(key, []), list):
            raise ValueError(f"'{key}' must be a list.")

    return config


def clean(job):
    """Keep only compact fields needed for storage."""
    fields = (
        "id",
        "company",
        "title",
        "location",
        "url",
        "updated_at",
        "source",
    )

    return {
        field: job.get(field, "")
        for field in fields
    }


def is_relevant(job, cfg):
    """Filter roles using target keywords and exclusion rules."""
    title = str(job.get("title") or "").strip().lower()
    description = str(job.get("description") or "").lower()

    if not title:
        return False

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

    excludes = [
        str(item).strip().lower()
        for item in cfg.get("exclude_keywords", [])
        if str(item).strip()
    ]

    if any(item in title for item in excludes):
        return False

    keywords = [
        str(item).strip().lower()
        for item in cfg.get("keywords", [])
        if str(item).strip()
    ]

    haystack = f"{title} {description}"
    return any(keyword in haystack for keyword in keywords)


def fetch_json(url):
    """Fetch JSON from a public job-board endpoint."""
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )

    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def extract_description(content):
    """Extract readable text from Greenhouse job content."""
    if not content:
        return ""

    import re
    from html import unescape

    text = re.sub(r"<[^>]+>", " ", str(content))
    text = unescape(text)
    return " ".join(text.split())


def discover_greenhouse(cfg):
    """Discover jobs from configured Greenhouse company boards."""
    discovered = []

    boards = cfg.get("greenhouse", cfg.get("boards", {}))

    if isinstance(boards, list):
        board_items = [
            (item.get("company", ""), item.get("token", ""))
            for item in boards
            if isinstance(item, dict)
        ]
    elif isinstance(boards, dict):
        board_items = list(boards.items())
    else:
        return discovered

    for company, token in board_items:
        if not token or not isinstance(token, str):
            continue

        url = (
            "https://boards-api.greenhouse.io/v1/boards/"
            f"{token}/jobs?content=true"
        )

        try:
            payload = fetch_json(url)
        except (HTTPError, URLError, TimeoutError, ValueError) as error:
            print(
                f"[WARN] Could not fetch {company or token}: {error}",
                file=sys.stderr,
            )
            continue

        for item in payload.get("jobs", []):
            location = item.get("location") or {}
            departments = item.get("departments") or []

            description = extract_description(item.get("content", ""))

            job = {
                "id": f"greenhouse:{token}:{item.get('id', '')}",
                "company": company or token,
                "title": item.get("title", ""),
                "location": location.get("name", "Not specified"),
                "url": item.get("absolute_url", ""),
                "updated_at": item.get("updated_at", ""),
                "source": "Greenhouse",
                "description": description,
                "departments": departments,
            }

            if is_relevant(job, cfg):
                discovered.append(clean(job))

    return discovered


def load_saved_jobs():
    """Load the previously saved job list."""
    if not JOBS_PATH.exists():
        return []

    try:
        with JOBS_PATH.open("r", encoding="utf-8") as file:
            jobs = json.load(file)

        return jobs if isinstance(jobs, list) else []
    except (json.JSONDecodeError, OSError):
        print(
            "[WARN] Existing jobs.json could not be read; "
            "starting with an empty list.",
            file=sys.stderr,
        )
        return []


def save_jobs(jobs):
    """Save jobs safely using a temporary file."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporary_path = JOBS_PATH.with_suffix(".tmp")

    with temporary_path.open("w", encoding="utf-8") as file:
        json.dump(jobs, file, indent=2, ensure_ascii=False)

    temporary_path.replace(JOBS_PATH)


def discover():
    """Fetch, filter, deduplicate, and save current matching jobs."""
    config = load_config()
    existing_jobs = load_saved_jobs()

    existing_by_id = {
        str(job.get("id")): job
        for job in existing_jobs
        if job.get("id")
    }

    fetched_jobs = discover_greenhouse(config)

    for job in fetched_jobs:
        job_id = str(job.get("id") or "")
        if job_id:
            existing_by_id[job_id] = job

    jobs = list(existing_by_id.values())
    jobs.sort(
        key=lambda item: (
            str(item.get("company", "")).lower(),
            str(item.get("title", "")).lower(),
        )
    )

    save_jobs(jobs)

    print(f"Fetched matching jobs: {len(fetched_jobs)}")
    print(f"Total unique saved jobs: {len(jobs)}")
    print(f"Saved to: {JOBS_PATH}")

    return jobs


def list_jobs():
    """Print saved jobs."""
    jobs = load_saved_jobs()

    if not jobs:
        print("No saved jobs. Run discovery first.")
        return

    for index, job in enumerate(jobs, start=1):
        print(
            f"{index}. {job.get('title', 'Untitled')} "
            f"| {job.get('company', 'Unknown company')} "
            f"| {job.get('location', 'Not specified')}"
        )
        print(f"   {job.get('url', '')}")


def main():
    command = sys.argv[1].lower() if len(sys.argv) > 1 else "discover"

    try:
        if command == "discover":
            discover()
        elif command == "list":
            list_jobs()
        else:
            print("Usage: python -m src.radar [discover|list]")
            raise SystemExit(2)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
