"""
Verify job postings are still active before showing them to users.

Strategy:
- Greenhouse / Lever / Ashby / SmartRecruiters: ALWAYS trusted (live API-backed boards).
- LinkedIn / Indeed / ZipRecruiter / Glassdoor listing URLs: SKIP HTTP check (bot-blocked,
  require auth). Rely on hours_old scrape freshness instead.
- All other URLs (company Workday, iCIMS, Taleo, etc.): HTTP GET check.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

import requests

log = logging.getLogger(__name__)

# ── Domains that are always live (ATS-backed) ──────────────────────────────────
TRUSTED_DOMAINS = [
    "boards.greenhouse.io",
    "greenhouse.io",
    "jobs.lever.co",
    "lever.co",
    "ashbyhq.com",
    "app.ashbyhq.com",
    "jobs.ashbyhq.com",
    "smartrecruiters.com",
    "myworkdayjobs.com",
    "workday.com",
    "icims.com",
    "taleo.net",
    "successfactors.com",
]

# ── Job-board listing URLs — can't verify without auth, skip HTTP check ────────
UNVERIFIABLE_BOARDS = [
    "linkedin.com",
    "indeed.com",
    "ziprecruiter.com",
    "glassdoor.com",
    "monster.com",
    "careerbuilder.com",
    "dice.com",
    "simplyhired.com",
]

# ── Page text that means the posting is closed ─────────────────────────────────
EXPIRED_PHRASES = [
    "no longer accepting",
    "job posting has expired",
    "this job has expired",
    "this job is closed",
    "position has been filled",
    "no longer available",
    "posting has been removed",
    "job is no longer",
    "this position is no longer",
    "application period has closed",
    "not currently accepting",
    "listing has expired",
    "this role has been filled",
    "job has been removed",
    "this job listing is expired",
    "application is closed",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}


def _domain(url):
    try:
        return urlparse(url).netloc.lower().lstrip("www.")
    except Exception:
        return ""


def _is_trusted(url):
    d = _domain(url)
    return any(t in d for t in TRUSTED_DOMAINS)


def _is_unverifiable_board(url):
    d = _domain(url)
    return any(b in d for b in UNVERIFIABLE_BOARDS)


def _http_check(url):
    """Return True if URL appears live, False if it looks expired."""
    try:
        resp = requests.get(url, timeout=8, headers=HEADERS, allow_redirects=True)
        if resp.status_code in (404, 410):
            return False
        text = resp.text.lower()
        return not any(phrase in text for phrase in EXPIRED_PHRASES)
    except Exception:
        return True  # network error → keep, let submission handle it


def _check_job(job):
    """
    Return (job, is_active).
    Check priority: url_direct → url.
    Skip unverifiable job-board listing URLs.
    """
    url_direct  = job.get("url_direct") or ""
    url_listing = job.get("url") or ""

    # Pick the best URL to check
    check_url = url_direct if url_direct else url_listing

    if not check_url:
        return job, True  # no URL at all, let it through

    if _is_trusted(check_url):
        return job, True

    if _is_unverifiable_board(check_url):
        # Can't verify — fall back to direct URL if different
        if url_direct and url_direct != url_listing and not _is_unverifiable_board(url_direct):
            if _is_trusted(url_direct):
                return job, True
            return job, _http_check(url_direct)
        # No verifiable URL at all → trust it (scraped fresh, hours_old=24)
        return job, True

    # Regular company ATS URL — do HTTP check
    is_active = _http_check(check_url)

    # Double-check with listing URL before declaring expired
    if not is_active and url_listing and url_listing != check_url:
        if not _is_unverifiable_board(url_listing):
            is_active = _http_check(url_listing)

    return job, is_active


def filter_active(jobs, max_workers=20):
    """
    Filter jobs to only those whose posting URL is still active.
    Returns (active_jobs, expired_job_ids).

    Greenhouse/Lever/Ashby are trusted and skip HTTP.
    LinkedIn/Indeed/ZipRecruiter listing URLs are skipped (can't verify without auth).
    """
    if not jobs:
        return [], []

    active      = []
    expired_ids = []

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_check_job, j): j for j in jobs}
        for future in as_completed(futures):
            try:
                job, is_active = future.result()
            except Exception:
                job = futures[future]
                is_active = True
            if is_active:
                active.append(job)
            else:
                expired_ids.append(job["job_id"])
                log.info(f"Expired detected: {job.get('company')} — {job.get('title')}")

    if expired_ids:
        log.info(f"Removed {len(expired_ids)} expired, kept {len(active)}")

    return active, expired_ids
