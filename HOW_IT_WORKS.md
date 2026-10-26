# Job Agent — Complete Guide

Automated job search agent for Jeevan Sai Kodamati. Scrapes job boards daily, screens for visa eligibility and ATS fit, tailors resumes using GPT-4o, submits applications via browser automation, watches Gmail for recruiter replies, and routes everything through a Telegram bot for one-tap approval.

---

## How It Works — Full Flow

```
Job Boards (LinkedIn / Indeed / Glassdoor / ZipRecruiter)
        │
        ▼
  [1] SCRAPER          — finds new job postings matching your target roles
        │
        ▼
  [2] VISA SCREENER    — auto-rejects disqualifying JDs (no sponsorship, ITAR, 1099 only)
        │
        ▼
  [3] ATS SCORER       — GPT-4o-mini scores your skill match against the JD (0–100%)
        │
        ▼  (skip if score < 65%)
  [4] RESUME TAILOR    — GPT-4o rewrites your resume in LaTeX, compiles to PDF
        │
        ▼
  [5] TELEGRAM BOT     — sends you job card with score + [✅ Apply] [❌ Skip]
        │
        ▼  (on your ✅)
  [6] PLAYWRIGHT       — fills and submits the application form automatically
        │
        ▼
  [7] GMAIL WATCHER    — monitors inbox for recruiter replies
        │
        ▼
  [8] EMAIL CLASSIFIER — GPT-4o-mini categorizes email + drafts a reply
        │
        ▼
  [9] TELEGRAM BOT     — sends email card with draft + [✅ Send] [✏️ Edit] [❌ Skip]
        │
        ▼  (on your ✅)
 [10] GMAIL SENDER     — sends the approved reply in the same thread
```

---

## Project Structure

```
Jobs Agent/
├── config.py                  # All settings — candidate profile, visa rules, skills
├── main.py                    # Orchestrator — runs all pipelines
├── requirements.txt           # Python dependencies
├── .env                       # Your API keys (never commit this)
├── .env.example               # Template for .env
│
├── scraper/
│   └── linkedin_scraper.py    # Scrapes LinkedIn, Indeed, Glassdoor, ZipRecruiter
│
├── screener/
│   └── visa_ats_screen.py     # Visa hard-stop check + GPT ATS scoring
│
├── resume/
│   ├── master_resume.tex      # Your master LaTeX resume
│   └── tailor.py              # GPT-4o tailoring → LaTeX → PDF
│
├── submitter/
│   └── playwright_submit.py   # Browser automation for form submission
│
├── gmail/
│   ├── watcher.py             # Gmail API — fetches unread recruiter emails
│   ├── classifier.py          # GPT-4o-mini email classifier + draft reply
│   └── reply_sender.py        # Sends approved replies via Gmail API
│
├── approval/
│   └── telegram_bot.py        # Telegram inline-button approval interface
│
├── db/
│   ├── __init__.py            # SQLite helper functions
│   └── schema.sql             # Database schema reference
│
├── output/                    # Tailored resumes saved here (auto-created)
│
└── .github/
    └── workflows/
        └── daily_apply.yml    # GitHub Actions — runs 9 AM EST Mon–Fri
```

---

## Module Details

### 1. Scraper (`scraper/linkedin_scraper.py`)
- Uses `python-jobspy` to pull from LinkedIn, Indeed, Glassdoor, and ZipRecruiter simultaneously
- Searches 6 target roles: Data Engineer, GCP Data Engineer, AI Platform Engineer, Full Stack Data Engineer, Senior Data Engineer, Cloud Data Engineer
- Fetches 20 results per search term = up to 120 job postings per run
- Deduplicates by MD5 hash of company + title + URL
- Skips jobs already in the database (no duplicate applications)

### 2. Visa Screener (`screener/visa_ats_screen.py`)
Two-tier check — no LLM needed, runs instantly:

**Hard Stop (auto-reject, no notification):**
| Phrase in JD | Reason |
|---|---|
| "no sponsorship" / "without sponsorship" | Cannot sponsor OPT extension/H-1B |
| "U.S. citizenship required" | F-1 is not a citizen |
| "ITAR" / "secret clearance" / "top secret" | Defense/classified — citizenship required |
| "1099 only" / "C2C only" | F-1 OPT requires W-2 employment |
| "F-1 not eligible" / "OPT not eligible" | Explicit exclusion |
| "2027 new grad" / "class of 2027 only" | Wrong graduation year |

**Gray Area (flagged, still forwarded for review):**
| Phrase | Action |
|---|---|
| "may not be able to employ" | Sent to Telegram with ⚠️ warning |
| "legally authorized" | Neutral — OPT counts, forwarded normally |
| "work authorization required" | Neutral — forwarded normally |

### 3. ATS Scorer (`screener/visa_ats_screen.py`)
- Sends JD + your full skill list to GPT-4o-mini
- Returns: score (0–100), matched keywords, missing required skills, recommendation
- **Minimum threshold: 65%** — jobs below this are auto-skipped
- Strict rule: never suggests fabricating skills

### 4. Resume Tailor (`resume/tailor.py`)
- Reads `resume/master_resume.tex` (your actual master resume)
- Sends JD + master resume to GPT-4o with tailoring rules:
  - Rewrite and emphasize skills you already have
  - Bridge adjacent experience honestly
  - Never fabricate: YARA, dbt, Rust, Fivetran, Alteryx, SAS, MISP, STIX, Snowflake, C++, C#, Kubernetes, MariaDB, SSIS, Spring Boot
  - Keep single-page, 10pt mathptmx, 0.25in margins
- Saves tailored `.tex` to `output/{company}_{title}_{date}.tex`
- Compiles to `.pdf` via `pdflatex`

### 5. Playwright Submitter (`submitter/playwright_submit.py`)
Supports these ATS platforms:
| Platform | Supported |
|---|---|
| Greenhouse | ✅ |
| Lever | ✅ |
| LinkedIn Easy Apply | ✅ |
| Ashby | ✅ |
| Generic | ✅ (best effort) |

Work authorization answers pre-filled:
- "Authorized to work in US?" → **Yes**
- "Require sponsorship now?" → **No** (OPT is current authorization)
- "Require sponsorship in the future?" → **Yes** (will need H-1B)
- "1099/contractor only?" → Skips application if no W-2 option

### 6. Gmail Watcher (`gmail/watcher.py`)
- Authenticates via Gmail OAuth2 (one-time browser login, then token auto-refreshes)
- Searches inbox for: `is:unread (opportunity OR position OR role OR interview OR screening OR offer OR recruiter) -from:me`
- Returns sender, subject, body preview (500 chars), thread ID
- Marks emails as read after processing

### 7. Email Classifier (`gmail/classifier.py`)
GPT-4o-mini classifies each email into one of five categories:

| Category | Action | Draft Reply |
|---|---|---|
| `recruiter_outreach` | Telegram notification | Expresses interest, confirms F-1 STEM OPT + W-2, asks for JD |
| `interview_invite` | Telegram notification (URGENT) | Confirms availability, asks format/duration |
| `rejection` | Logged silently | Brief gracious acknowledgment (optional) |
| `offer` | Telegram URGENT alert | **No auto-draft** — manual review required |
| `unknown` | Telegram notification | Neutral clarification request |

### 8. Telegram Approval Bot (`approval/telegram_bot.py`)

**Job approval card:**
```
🔍 NEW JOB FOUND
Company: Acme Corp
Role: Senior Data Engineer
Location: Remote
ATS Score: 78%
Visa: ✅ OK

Matched: Python, BigQuery, Kafka, Airflow, Terraform
Missing: dbt

Notes: Strong GCP stack match. dbt absent but not required.

[✅ Apply]  [❌ Skip]
```

**Email approval card:**
```
📧 RECRUITER EMAIL
From: edgar@cirruslabs.com
Subject: AI Engineer — $70/hr Contract
Category: Recruiter Outreach
Company: CirrusLabs | Role: AI Engineer

Preview: Hi Jeevan, I came across your profile...

Draft Reply:
Hi Edgar, thank you for reaching out...

[✅ Send Reply]  [✏️ Edit Reply]  [❌ Skip]
```

**Edit flow:** Tap ✏️ Edit Reply → bot prompts you → type your reply → bot sends it.

---

## Setup — Step by Step

### Step 1 — Install Python dependencies
```bash
cd "/Users/HP/Documents/Jobs Agent"
pip install -r requirements.txt
playwright install chromium
```

### Step 2 — Create your `.env` file
```bash
cp .env.example .env
```
Open `.env` and fill in:
```
OPENAI_API_KEY=sk-...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

### Step 3 — Get your OpenAI API key
1. Go to platform.openai.com → API Keys
2. Create new key → paste into `.env`
3. Make sure your account has credits (GPT-4o-mini is very cheap — ~$0.002/job screened)

### Step 4 — Create your Telegram bot
1. Open Telegram → search **@BotFather**
2. Send `/newbot` → follow prompts → copy the token → paste as `TELEGRAM_BOT_TOKEN` in `.env`
3. Message **@userinfobot** on Telegram → it replies with your numeric chat ID → paste as `TELEGRAM_CHAT_ID`
4. Start your bot by sending it any message

### Step 5 — Set up Gmail API
1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a new project (e.g. "Job Agent")
3. Search "Gmail API" → Enable it
4. Go to **Credentials** → Create Credentials → **OAuth 2.0 Client ID**
5. Application type: **Desktop App** → Create
6. Download the JSON → rename to `credentials.json` → place in the project root
7. First time you run the Gmail pipeline, a browser window opens for Google login — sign in and authorize
8. `token.json` is created automatically and reused from then on

### Step 6 — Test with dry run (no submissions, no emails sent)
```bash
python main.py --dry-run
```
You'll see all the pipeline steps logged without any real actions taken.

### Step 7 — Run for real
```bash
python main.py              # full pipeline (jobs + Gmail + Telegram bot)
python main.py --jobs-only  # scrape and apply only
python main.py --gmail-only # Gmail watch and classify only
python main.py --bot-only   # Telegram bot only (approve queued items)
```

---

## GitHub Actions — Runs Automatically Every Weekday

The workflow at `.github/workflows/daily_apply.yml` runs at **9 AM EST Monday–Friday**.

### Setup for GitHub Actions
1. Push this project to a private GitHub repository
2. Go to **Settings → Secrets and variables → Actions**
3. Add these secrets:
   - `OPENAI_API_KEY`
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
4. For Gmail: you need to commit `credentials.json` and a pre-authorized `token.json`
   - Run locally once to generate `token.json`, then add both files to the repo (private repo only — never public)
5. Go to **Actions** tab → enable workflows → done

You can also trigger a manual run anytime from the Actions tab using **workflow_dispatch**.

---

## Database

SQLite database at `db/applications.db` — created automatically on first run.

**`applications` table** — one row per job found:
| Column | Description |
|---|---|
| `job_id` | MD5 hash of company+title+URL (dedup key) |
| `company` | Company name |
| `title` | Job title |
| `location` | Job location |
| `url` | Direct job posting URL |
| `ats_score` | ATS match score (0–100) |
| `visa_flag` | `ok`, `gray_area`, or `hard_stop` |
| `status` | `pending`, `applied`, `skipped`, `failed` |
| `resume_tex_path` | Path to tailored .tex file |
| `resume_pdf_path` | Path to compiled .pdf |

**`emails` table** — one row per recruiter email:
| Column | Description |
|---|---|
| `gmail_message_id` | Gmail message ID (dedup key) |
| `sender` | Recruiter's email address |
| `subject` | Email subject |
| `category` | `recruiter_outreach`, `interview_invite`, `rejection`, `offer`, `unknown` |
| `draft_reply` | GPT-generated reply |
| `status` | `pending`, `approved`, `sent`, `skipped` |
| `reply_sent` | Final text that was sent |

---

## Configuration (`config.py`)

Key settings you can change:

```python
# Minimum ATS score to proceed (default: 65%)
"min_ats_score": 65

# How many results per search term per site (default: 20)
"results_per_term": 20

# Job boards to scrape
"sites": ["linkedin", "indeed", "glassdoor", "zip_recruiter"]

# Search terms
"search_terms": [
    "Data Engineer",
    "GCP Data Engineer",
    "AI Platform Engineer",
    "Full Stack Data Engineer",
    "Senior Data Engineer",
    "Cloud Data Engineer",
]
```

---

## Cost Estimate

| Operation | Model | Cost per run |
|---|---|---|
| ATS scoring (up to 120 jobs) | GPT-4o-mini | ~$0.05 |
| Resume tailoring (qualifying jobs, ~10–20) | GPT-4o | ~$0.30–$0.60 |
| Email classification (per email) | GPT-4o-mini | ~$0.001 |
| **Total per day** | | **~$0.40–$0.70** |

Monthly cost running every weekday: **~$8–$15/month**.

---

## Safety Rules Built In

- **Never applies without your approval** — every job goes through Telegram first
- **Never sends emails without your approval** — every reply goes through Telegram first
- **Never fabricates skills** — the tailoring prompt explicitly forbids inventing experience
- **Visa hard stops are automatic** — no-sponsorship JDs are rejected before any LLM call
- **Dry run mode** — test the full pipeline without any real submissions or sends
- **Duplicate prevention** — SQLite tracks every job and email; nothing is processed twice
- **Offer emails are never auto-drafted** — flagged URGENT, always manual
