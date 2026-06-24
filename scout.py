"""
Job Scout — scrapes ATS career pages before jobs hit LinkedIn.

Supports: Greenhouse · Lever · Ashby · Web search discovery (Serper.dev)
Outputs:  HTML email digest via Gmail SMTP

Usage:
    python scout.py              # Run with env vars set
    python scout.py --dry-run    # Print matches, skip email + state save
"""

import argparse
import json
import os
import re
import smtplib
import time
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests

# ── Paths ─────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).parent
COMPANIES_FILE = ROOT / "companies.json"
SEEN_JOBS_FILE = ROOT / "data" / "seen_jobs.json"

# ── Relevance filters ─────────────────────────────────────────────────────────

# At least one from each list must appear in the job title.
SENIORITY = [
    "director", "head of", "vp ", "vp,", "vice president",
    "principal", "senior", "sr.", "lead",
]
DOMAIN = [
    "design", "ux", "user experience", "product design",
    "ai product", "human-centered", "hci",
]

# Any of these in the title = instant match (no seniority check needed).
EXACT_PHRASES = [
    "director of design", "head of design", "vp design", "vp of design",
    "chief design officer", "director of product design",
    "head of product design", "director of ux", "head of ux",
]

# Any of these in the title = hard exclude.
EXCLUDE = [
    "software engineer", "data engineer", "data scientist",
    "machine learning engineer", "ml engineer", "devops", "sre",
    "site reliability", "sales", "account executive", "finance",
    "legal", "human resources", " hr ", "marketing manager",
    "accountant", "recruiter", "talent acquisition",
    "data analyst", "business analyst", "solutions engineer",
]


def is_relevant(job: dict) -> bool:
    title = job["title"].lower()

    # Hard excludes first
    if any(kw in title for kw in EXCLUDE):
        return False

    # Exact phrase = auto-match
    if any(phrase in title for phrase in EXACT_PHRASES):
        return True

    # Must have seniority + domain
    has_seniority = any(kw in title for kw in SENIORITY)
    has_domain = any(kw in title for kw in DOMAIN)
    return has_seniority and has_domain


# ── ATS scrapers ──────────────────────────────────────────────────────────────

def scrape_greenhouse(slug: str, name: str) -> list[dict]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    try:
        r = requests.get(url, timeout=12, headers={"User-Agent": "JobScout/1.0"})
        r.raise_for_status()
        return [
            {
                "id": f"gh_{j['id']}",
                "title": j["title"],
                "company": name,
                "location": j.get("location", {}).get("name", ""),
                "url": j["absolute_url"],
                "posted_at": j.get("updated_at", ""),
                "source": "greenhouse",
            }
            for j in r.json().get("jobs", [])
        ]
    except Exception as e:
        print(f"  [WARN] Greenhouse '{slug}': {e}")
        return []


def scrape_lever(slug: str, name: str) -> list[dict]:
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    try:
        r = requests.get(url, timeout=12, headers={"User-Agent": "JobScout/1.0"})
        r.raise_for_status()
        jobs = []
        for j in r.json():
            ms = j.get("createdAt", 0)
            posted = (
                datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()
                if ms else ""
            )
            jobs.append({
                "id": f"lv_{j['id']}",
                "title": j["text"],
                "company": name,
                "location": j.get("categories", {}).get("location", ""),
                "url": j["hostedUrl"],
                "posted_at": posted,
                "source": "lever",
            })
        return jobs
    except Exception as e:
        print(f"  [WARN] Lever '{slug}': {e}")
        return []


def scrape_ashby(slug: str, name: str) -> list[dict]:
    url = "https://api.ashbyhq.com/posting-api/job-board"
    try:
        r = requests.post(
            url,
            json={"organizationHostedJobsPageName": slug},
            timeout=12,
            headers={"User-Agent": "JobScout/1.0"},
        )
        r.raise_for_status()
        jobs = []
        for j in r.json().get("jobPostings", []):
            loc = "Remote" if j.get("isRemote") else (
                (j.get("location") or {}).get("name", "")
            )
            jobs.append({
                "id": f"ash_{j['id']}",
                "title": j["title"],
                "company": name,
                "location": loc,
                "url": j.get("jobUrl") or f"https://jobs.ashbyhq.com/{slug}/{j['id']}",
                "posted_at": j.get("publishedAt", ""),
                "source": "ashby",
            })
        return jobs
    except Exception as e:
        print(f"  [WARN] Ashby '{slug}': {e}")
        return []


# ── Web search discovery (finds companies not in your list) ───────────────────

DISCOVERY_QUERIES = [
    # ATS platforms — existing companies + unknown ones
    'site:greenhouse.io "director" "design" "health"',
    'site:greenhouse.io "head of design" OR "vp design" health',
    'site:lever.co "director" "design" health',
    'site:lever.co "head of design" OR "vp of design"',
    'site:ashbyhq.com "director" "design" health',
    'site:ashbyhq.com "head of design" OR "vp design"',
    # Wellfound (startup-first, posts before LinkedIn)
    'site:wellfound.com "director" "design" health',
    'site:wellfound.com "head of design" OR "vp design" startup',
    'site:wellfound.com "senior product designer" health AI',
    # Broad discovery
    '"director of design" "digital health" job posting 2025',
    '"head of design" "AI" "product" job 2025',
]


def _company_from_url(url: str) -> str:
    for pattern in [
        r"greenhouse\.io/([^/?#]+)",
        r"lever\.co/([^/?#]+)",
        r"ashbyhq\.com/([^/?#]+)",
        r"wellfound\.com/company/([^/?#]+)",
    ]:
        m = re.search(pattern, url)
        if m:
            return m.group(1).replace("-", " ").title()
    return "Unknown"


def search_web_discovery(serper_key: str) -> list[dict]:
    headers = {"X-API-KEY": serper_key, "Content-Type": "application/json"}
    found = []
    for query in DISCOVERY_QUERIES:
        try:
            r = requests.post(
                "https://google.serper.dev/search",
                headers=headers,
                json={"q": query, "num": 10},
                timeout=15,
            )
            for result in r.json().get("organic", []):
                url = result.get("link", "")
                found.append({
                    "id": f"web_{abs(hash(url)) % 10**9}",
                    "title": result.get("title", ""),
                    "company": _company_from_url(url),
                    "location": "",
                    "url": url,
                    "posted_at": "",
                    "source": "web_discovery",
                })
            time.sleep(0.4)
        except Exception as e:
            print(f"  [WARN] Serper query failed: {e}")
    return found


def scrape_yc_jobs() -> list[dict]:
    """
    Scrape YC Work at a Startup — health + AI design roles.
    YC-backed companies post here first; strong signal for well-funded startups.

    Parses the __NEXT_DATA__ JSON that Next.js embeds in the page HTML.
    Falls back gracefully if the page structure changes.
    """
    import json as _json

    searches = [
        "https://www.workatastartup.com/jobs?q=product+designer&industry=Healthcare",
        "https://www.workatastartup.com/jobs?q=ux+designer&industry=Healthcare",
        "https://www.workatastartup.com/jobs?q=director+design",
    ]

    jobs = []
    seen_ids: set[str] = set()

    for url in searches:
        try:
            r = requests.get(
                url,
                timeout=15,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    ),
                    "Accept": "text/html,application/xhtml+xml",
                },
            )
            r.raise_for_status()

            # Extract __NEXT_DATA__ JSON blob embedded by Next.js
            m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(\{.*?\})</script>', r.text, re.DOTALL)
            if not m:
                print(f"  [WARN] YC: no __NEXT_DATA__ found at {url}")
                continue

            data = _json.loads(m.group(1))
            # Path varies by Next.js version — try both common structures
            postings = (
                data.get("props", {}).get("pageProps", {}).get("jobPostings")
                or data.get("props", {}).get("pageProps", {}).get("jobs")
                or []
            )

            for j in postings:
                job_id = f"yc_{j.get('id', abs(hash(str(j))) % 10**9)}"
                if job_id in seen_ids:
                    continue
                seen_ids.add(job_id)

                company = (j.get("company") or {}).get("name", "") or j.get("companyName", "")
                location = j.get("locationDescription") or j.get("location") or ""
                if j.get("remote"):
                    location = location or "Remote"

                jobs.append({
                    "id": job_id,
                    "title": j.get("title", ""),
                    "company": company,
                    "location": location,
                    "url": j.get("url") or f"https://www.workatastartup.com/jobs/{j.get('id', '')}",
                    "posted_at": j.get("createdAt", ""),
                    "source": "yc",
                })

            time.sleep(0.5)

        except Exception as e:
            print(f"  [WARN] YC scrape failed for {url}: {e}")

    print(f"  {'YC Work at a Startup':30s} {len(jobs):>3} jobs")
    return jobs


# ── State management ──────────────────────────────────────────────────────────

def load_seen() -> set[str]:
    if SEEN_JOBS_FILE.exists():
        return set(json.loads(SEEN_JOBS_FILE.read_text()))
    return set()


def save_seen(seen: set[str]) -> None:
    SEEN_JOBS_FILE.parent.mkdir(exist_ok=True)
    SEEN_JOBS_FILE.write_text(json.dumps(sorted(seen), indent=2))


# ── Email ─────────────────────────────────────────────────────────────────────

SOURCE_LABELS = {
    "greenhouse": ("🌱", "Greenhouse"),
    "lever": ("⚙️", "Lever"),
    "ashby": ("🔷", "Ashby"),
    "web_discovery": ("🌐", "Web"),
    "yc": ("🚀", "YC Startup"),
}

HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
    color: #111; background: #f8f8f8; margin: 0; padding: 24px;
  }}
  .wrap {{ max-width: 620px; margin: 0 auto; background: #fff;
           border-radius: 10px; padding: 32px; box-shadow: 0 1px 4px rgba(0,0,0,.08); }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }}
  .sub {{ color: #666; font-size: 13px; margin-bottom: 28px; }}
  .job {{ border: 1px solid #e8e8e8; border-radius: 8px; padding: 14px 16px;
          margin-bottom: 14px; background: #fff; }}
  .job-title {{ font-size: 15px; font-weight: 600; color: #0055cc;
                text-decoration: none; display: block; margin-bottom: 4px; }}
  .job-title:hover {{ text-decoration: underline; }}
  .job-meta {{ font-size: 13px; color: #555; }}
  .badge {{ display: inline-block; font-size: 11px; font-weight: 600;
            letter-spacing: .4px; padding: 2px 7px; border-radius: 4px;
            margin-left: 6px; vertical-align: middle; }}
  .gh {{ background: #d1fae5; color: #065f46; }}
  .lv {{ background: #dbeafe; color: #1e40af; }}
  .ash {{ background: #ede9fe; color: #5b21b6; }}
  .web {{ background: #fef9c3; color: #854d0e; }}
  .yc {{ background: #fee2e2; color: #991b1b; }}
  .empty {{ color: #888; font-size: 14px; padding: 16px 0; }}
  .footer {{ margin-top: 28px; font-size: 12px; color: #aaa; border-top: 1px solid #eee; padding-top: 14px; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>🎯 Job Scout — {date}</h1>
  <p class="sub">{count} new match{plural} · scraped directly from ATS pages (24–72 hrs ahead of LinkedIn)</p>
  {body}
  <div class="footer">
    Sources: Greenhouse · Lever · Ashby · Wellfound · YC Work at a Startup · Web discovery via Serper<br>
    Filters: Director / Head of / VP / Principal / Senior · Design, UX, Product Design, AI Product
  </div>
</div>
</body>
</html>
"""

BADGE_CLASS = {"greenhouse": "gh", "lever": "lv", "ashby": "ash", "web_discovery": "web", "yc": "yc"}


def build_email_html(new_jobs: list[dict]) -> str:
    if not new_jobs:
        body = '<p class="empty">No new matches today — scout will keep watching.</p>'
    else:
        cards = []
        for j in new_jobs:
            icon, label = SOURCE_LABELS.get(j["source"], ("•", j["source"]))
            bc = BADGE_CLASS.get(j["source"], "web")
            loc = j.get("location") or "Remote / Not specified"
            co = j.get("company", "")
            cards.append(
                f'<div class="job">'
                f'<a class="job-title" href="{j["url"]}">{j["title"]}</a>'
                f'<span class="job-meta">{co} · {loc}'
                f'<span class="badge {bc}">{icon} {label}</span>'
                f'</span>'
                f'</div>'
            )
        body = "\n".join(cards)

    count = len(new_jobs)
    return HTML_TEMPLATE.format(
        date=datetime.now().strftime("%B %d, %Y"),
        count=count,
        plural="es" if count != 1 else "",
        body=body,
    )


def send_email(html: str, count: int) -> None:
    to_addr    = os.environ["TO_EMAIL"]
    from_addr  = os.environ["FROM_EMAIL"]
    password   = os.environ["GMAIL_APP_PASSWORD"]

    subject = (
        f"🎯 Job Scout: {count} new match{'es' if count != 1 else ''} "
        f"— {datetime.now().strftime('%b %d')}"
        if count else
        f"Job Scout: no new matches today ({datetime.now().strftime('%b %d')})"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = from_addr
    msg["To"]      = to_addr
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(from_addr, password)
        smtp.sendmail(from_addr, to_addr, msg.as_string())
    print(f"✓ Email sent: {subject}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main(dry_run: bool = False) -> None:
    companies = json.loads(COMPANIES_FILE.read_text())
    seen = load_seen()

    all_jobs: list[dict] = []
    scrapers = {"greenhouse": scrape_greenhouse, "lever": scrape_lever, "ashby": scrape_ashby}

    for co in companies:
        fn = scrapers.get(co["ats"])
        if not fn:
            continue
        jobs = fn(co["slug"], co["name"])
        print(f"  {co['name']:30s} {len(jobs):>3} jobs")
        all_jobs.extend(jobs)

    serper_key = os.getenv("SERPER_API_KEY")
    if serper_key:
        web = search_web_discovery(serper_key)
        print(f"  {'Web discovery':30s} {len(web):>3} results")
        all_jobs.extend(web)

    yc_jobs = scrape_yc_jobs()
    all_jobs.extend(yc_jobs)

    relevant  = [j for j in all_jobs if is_relevant(j)]
    new_jobs  = [j for j in relevant if j["id"] not in seen]

    print(f"\nTotal scraped: {len(all_jobs)} | Relevant: {len(relevant)} | New: {len(new_jobs)}")

    if dry_run:
        print("\n── Dry run — new matches ──────────────────")
        for j in new_jobs:
            print(f"  [{j['source']:12s}] {j['title']} @ {j['company']}")
            print(f"              {j['url']}")
        return

    # Mark all relevant as seen (not just new ones — prevents re-alerting)
    seen.update(j["id"] for j in relevant)
    save_seen(seen)

    html = build_email_html(new_jobs)

    send_empty = os.getenv("SEND_EMPTY_DIGEST", "false").lower() == "true"
    if new_jobs or send_empty:
        send_email(html, len(new_jobs))
    else:
        print("No new jobs today — skipping email.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Print matches, skip email")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
