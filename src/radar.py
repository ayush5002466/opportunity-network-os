import argparse
import json
import os
import re
import smtplib
import sqlite3
import sys
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urlparse

import requests

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CONFIG = ROOT / "config" / "boards.json"
STATE = DATA / "jobs.json"
DB = DATA / "tracker.db"
DATA.mkdir(exist_ok=True)

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def db_conn():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute("""
      CREATE TABLE IF NOT EXISTS applications (
        job_id TEXT PRIMARY KEY, title TEXT NOT NULL, company TEXT NOT NULL,
        url TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'discovered',
        notes TEXT DEFAULT '', updated_at TEXT NOT NULL
      )
    """)
    conn.execute("""
      CREATE TABLE IF NOT EXISTS contacts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
        organization TEXT DEFAULT '', channel TEXT DEFAULT '',
        reason TEXT DEFAULT '', status TEXT DEFAULT 'to_reach_out',
        follow_up TEXT DEFAULT '', created_at TEXT NOT NULL
      )
    """)
    conn.commit()
    return conn

def load_config():
    if not CONFIG.exists():
        raise SystemExit("Missing config/boards.json. Copy config/boards.example.json to config/boards.json first.")
    return json.loads(CONFIG.read_text(encoding="utf-8"))

def load_state():
    if not STATE.exists():
        return {}
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}

def fetch_greenhouse(company, token):
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
    r = requests.get(url, timeout=25)
    r.raise_for_status()
    result = []
    for j in r.json().get("jobs", []):
        result.append({
            "id": f"gh:{token}:{j.get('id')}",
            "company": company,
            "title": j.get("title", ""),
            "location": (j.get("location") or {}).get("name", ""),
            "url": j.get("absolute_url", ""),
            "updated_at": j.get("updated_at", ""),
            "description": re.sub(r"<[^>]+>", " ", j.get("content", "") or ""),
            "source": "Greenhouse"
        })
    return result

def fetch_lever(company, site):
    url = f"https://api.lever.co/v0/postings/{site}?mode=json"
    r = requests.get(url, timeout=25)
    r.raise_for_status()
    result = []
    for j in r.json():
        cats = j.get("categories") or {}
        desc = " ".join([j.get("descriptionPlain", ""), j.get("additional", "") or ""])
        result.append({
            "id": f"lever:{site}:{j.get('id')}",
            "company": company,
            "title": j.get("text", ""),
            "location": cats.get("location", ""),
            "url": j.get("hostedUrl", ""),
            "updated_at": j.get("createdAt", ""),
            "description": re.sub(r"<[^>]+>", " ", desc),
            "source": "Lever"
        })
    return result

def is_relevant(job, cfg):
    haystack = f"{job.get('title','')} {job.get('description','')}".lower()
    keywords = [k.lower() for k in cfg.get("keywords", [])]
    excludes = [k.lower() for k in cfg.get("exclude_keywords", [])]
    return any(k in haystack for k in keywords) and not any(x in job.get("title", "").lower() for x in excludes)

def clean(job):
    # Keep state compact; source descriptions may be long and can change frequently.
    return {k: job.get(k, "") for k in ("id", "company", "title", "location", "url", "updated_at", "source")}

def send_telegram(text):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text[:3900], "disable_web_page_preview": True},
        timeout=20
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
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, sender, recipient
    msg.set_content(body)
    port = int(os.getenv("SMTP_PORT", "587"))
    with smtplib.SMTP(host, port, timeout=25) as server:
        server.starttls()
        server.login(user, password)
        server.send_message(msg)
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
    print("Notifications sent: " + (", ".join(sent) if sent else "none (configure credentials)"))

def discover():
    cfg = load_config()
    old = load_state()
    found, errors = [], []
    for item in cfg.get("greenhouse", []):
        try:
            found.extend(fetch_greenhouse(item["company"], item["token"]))
        except Exception as e:
            errors.append(f"Greenhouse {item.get('company','?')}: {e}")
    for item in cfg.get("lever", []):
        try:
            found.extend(fetch_lever(item["company"], item["site"]))
        except Exception as e:
            errors.append(f"Lever {item.get('company','?')}: {e}")
    relevant = [j for j in found if j.get("url") and is_relevant(j, cfg)]
    current = {j["id"]: clean(j) for j in relevant}
    new_jobs = [j for j in relevant if j["id"] not in old]
    STATE.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")

    conn = db_conn()
    for job in relevant:
        conn.execute("""
          INSERT OR IGNORE INTO applications(job_id,title,company,url,status,notes,updated_at)
          VALUES(?,?,?,?,?,?,?)
        """, (job["id"], job["title"], job["company"], job["url"], "discovered", "", now_iso()))
    conn.commit()
    conn.close()

    if new_jobs:
        lines = []
        for j in new_jobs[:30]:
            lines.append(f"- {j['title']} — {j['company']} | {j['location']}\n  {j['url']}\n  Source: {j['source']}")
        body = f"New potentially relevant public listings: {len(new_jobs)}\n\n" + "\n\n".join(lines)
        body += "\n\nVerify graduation year, work authorization, experience, deadline, and sponsorship on the official posting."
        notify(f"Job Radar: {len(new_jobs)} new listing(s)", body)
    else:
        print("No new relevant listings.")
    if errors:
        print("\nSource errors:\n" + "\n".join(errors), file=sys.stderr)
    print(f"Fetched {len(found)} listings; relevant {len(relevant)}; new {len(new_jobs)}.")
    return 1 if errors and not found else 0

def list_jobs():
    state = load_state()
    for j in sorted(state.values(), key=lambda x: (x.get("company",""), x.get("title",""))):
        print(f"{j['id']} | {j['company']} | {j['title']} | {j.get('location','')} | {j['url']}")

def add_application(args):
    conn = db_conn()
    conn.execute("""
      INSERT INTO applications(job_id,title,company,url,status,notes,updated_at)
      VALUES(?,?,?,?,?,?,?)
      ON CONFLICT(job_id) DO UPDATE SET status=excluded.status, notes=excluded.notes, updated_at=excluded.updated_at
    """, (args.job_id, args.title or args.job_id, args.company or "Unknown", args.url or "", args.status, args.notes or "", now_iso()))
    conn.commit()
    conn.close()
    print(f"Saved {args.job_id} as {args.status}.")

def applications():
    conn = db_conn()
    rows = conn.execute("SELECT job_id,title,company,status,url,updated_at FROM applications ORDER BY updated_at DESC").fetchall()
    conn.close()
    for r in rows:
        print(f"{r['status']:12} | {r['company']} | {r['title']} | {r['job_id']} | {r['url']}")

def add_contact(args):
    conn = db_conn()
    conn.execute("INSERT INTO contacts(name,organization,channel,reason,status,follow_up,created_at) VALUES(?,?,?,?,?,?,?)",
                 (args.name, args.organization or "", args.channel or "", args.reason or "", args.status, args.follow_up or "", now_iso()))
    conn.commit()
    conn.close()
    print(f"Saved contact: {args.name}")

def contacts():
    conn = db_conn()
    rows = conn.execute("SELECT name,organization,channel,reason,status,follow_up FROM contacts ORDER BY id DESC").fetchall()
    conn.close()
    for r in rows:
        print(f"{r['status']:14} | {r['name']} | {r['organization']} | {r['channel']} | reason: {r['reason']} | follow-up: {r['follow_up']}")

def main():
    p = argparse.ArgumentParser(description="Opportunity & Network OS")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("discover")
    sub.add_parser("list")
    sub.add_parser("applications")
    sub.add_parser("contacts")
    a = sub.add_parser("add-application")
    a.add_argument("--job-id", required=True)
    a.add_argument("--status", required=True, choices=["discovered","saved","to_apply","applied","assessment","interview","rejected","offer","closed"])
    a.add_argument("--title")
    a.add_argument("--company")
    a.add_argument("--url")
    a.add_argument("--notes")
    c = sub.add_parser("add-contact")
    c.add_argument("--name", required=True)
    c.add_argument("--organization")
    c.add_argument("--channel", help="Public profile, community, or event")
    c.add_argument("--reason", help="Specific genuine technical overlap")
    c.add_argument("--status", default="to_reach_out", choices=["to_reach_out","contacted","replied","follow_up","connected","no_response","do_not_contact"])
    c.add_argument("--follow-up")
    args = p.parse_args()
    {"discover": discover, "list": list_jobs, "applications": applications,
     "contacts": contacts, "add-application": lambda: add_application(args),
     "add-contact": lambda: add_contact(args)}[args.command]()

if __name__ == "__main__":
    main()
