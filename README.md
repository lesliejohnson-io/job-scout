# Job Scout

Scrapes ATS career pages (Greenhouse, Lever, Ashby) directly — typically 24–72 hours before jobs appear on LinkedIn or Indeed. Sends a daily email digest of new matches filtered for director-level design / AI product roles in digital health.

## How it works

1. Queries each company's public ATS API directly (no login required)
2. Filters by seniority + domain keywords (director, head of, VP + design, UX, product design, AI product)
3. Compares against previously seen jobs — you only hear about *new* postings
4. Sends an HTML email digest via Gmail
5. GitHub Actions runs it automatically every weekday at 5 AM ET

---

## One-time setup (~15 minutes)

### 1. Fork / clone this repo

```bash
git clone https://github.com/YOUR_USERNAME/job-scout.git
cd job-scout
```

### 2. Get a Gmail App Password

1. Go to [myaccount.google.com/security](https://myaccount.google.com/security)
2. Enable 2-Step Verification (if not already on)
3. Search "App passwords" → create one named "Job Scout"
4. Copy the 16-character password — you won't see it again

### 3. Get a Serper API key (free — optional but recommended)

Serper powers the web discovery queries that find companies *not* in your company list.

1. Sign up at [serper.dev](https://serper.dev) — free tier: 2,500 searches/month
2. Copy your API key from the dashboard

### 4. Add secrets to GitHub

Go to your repo → **Settings → Secrets and variables → Actions → New repository secret**

| Secret name | Value |
|---|---|
| `FROM_EMAIL` | Your Gmail address (e.g. `myName@gmail.com`) |
| `TO_EMAIL` | Where to send digests (can be same address) |
| `GMAIL_APP_PASSWORD` | The 16-char app password from Step 2 |
| `SERPER_API_KEY` | Serper key from Step 3 (optional) |

### 5. Run it once manually to verify

Go to **Actions → Job Scout Daily → Run workflow** — check your inbox in ~2 minutes.

---

## Local usage

```bash
pip install requests

# Dry run — prints matches, skips email and state update
python scout.py --dry-run

# Full run (requires env vars)
export FROM_EMAIL="..."
export TO_EMAIL="..."
export GMAIL_APP_PASSWORD="..."
export SERPER_API_KEY="..."   # optional
python scout.py
```

---

## Customize

### Add a company

1. Visit the company's careers page
2. Look at the URL — if it contains `greenhouse.io`, `lever.co`, or `ashbyhq.com`, note the slug
3. Add to `companies.json`:

```json
{ "name": "Acme Health", "slug": "acmehealth", "ats": "greenhouse" }
```

**Verify slugs:**
- Greenhouse: `https://boards.greenhouse.io/{slug}`
- Lever: `https://jobs.lever.co/{slug}`
- Ashby: `https://jobs.ashbyhq.com/{slug}`

### Adjust filters

Edit the top of `scout.py`:

```python
SENIORITY = ["director", "head of", ...]   # seniority tier to match
DOMAIN    = ["design", "ux", ...]          # domain area to match
EXACT_PHRASES = ["director of design", ...]  # auto-match any of these
EXCLUDE   = ["software engineer", ...]     # never match these
```

### Change schedule

Edit `.github/workflows/daily_scout.yml`:

```yaml
- cron: '0 9 * * 1-5'   # 9 AM UTC = 5 AM ET, Mon–Fri
```

Use [crontab.guru](https://crontab.guru) to build a custom schedule.

### Get an email even with no new matches

Set `SEND_EMPTY_DIGEST: "true"` in the workflow file.

---

## Why this beats LinkedIn job alerts

| | LinkedIn Alerts | Job Scout |
|---|---|---|
| **Timing** | 24–72 hrs after ATS posting | Same day as ATS posting |
| **Noise** | High (promoted jobs, irrelevant) | You control the filters |
| **Source** | Aggregated | Direct from company ATS |
| **Discovery** | Your network | Web search + curated list |
| **Free** | Yes | Yes (+ optional Serper free tier) |

---

## Troubleshooting

**No email received** — Check the Actions run log for errors. Common issues: wrong app password, Gmail 2FA not enabled.

**A company returns 0 jobs** — The slug may be wrong. Verify by visiting the ATS URL directly.

**Too many false positives** — Add terms to `EXCLUDE` in `scout.py`.

**Missing a company** — Add it to `companies.json`. If they use a different ATS (Workable, Rippling, etc.), open an issue.
