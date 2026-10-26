import hashlib
import logging
from datetime import datetime

from jobspy import scrape_jobs as jobspy_scrape

import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def _make_job_id(company, title, url):
    raw = f"{company}|{title}|{url}".lower()
    return hashlib.md5(raw.encode()).hexdigest()


def scrape_jobs(search_terms=None, location=None, remote=None):
    """
    search_terms: list of job title strings from user profile (falls back to config)
    location: preferred location string (falls back to config)
    remote: bool override (falls back to config)
    Returns list of job dicts — does NOT check DB (caller handles deduplication).
    """
    terms = search_terms if search_terms else config.JOB_SEARCH["search_terms"]
    loc = location if location else config.JOB_SEARCH["location"]
    is_remote = remote if remote is not None else config.JOB_SEARCH["remote"]

    all_jobs = []
    seen_ids = set()  # in-run dedup only

    for term in terms:
        log.info(f"Scraping: '{term}'")
        try:
            results = jobspy_scrape(
                site_name=config.JOB_SEARCH["sites"],
                search_term=term,
                location=loc,
                results_wanted=config.JOB_SEARCH["results_per_term"],
                is_remote=is_remote,
                country_indeed="USA",
                hours_old=24,
                linkedin_fetch_description=True,
                fetch_description=True,
            )
        except Exception as e:
            log.warning(f"Scrape failed for '{term}': {e}")
            continue

        if results is None or results.empty:
            log.info(f"  No results for '{term}'")
            continue

        count_new = 0
        for _, row in results.iterrows():
            company = str(row.get("company", "")).strip()
            title   = str(row.get("title", "")).strip()
            url     = str(row.get("job_url", "")).strip()

            raw_direct = row.get("job_url_direct")
            url_direct = (
                ""
                if (raw_direct is None or str(raw_direct).strip().lower() in ("nan", "none", ""))
                else str(raw_direct).strip()
            )

            raw_desc    = row.get("description")
            description = (
                ""
                if (raw_desc is None or str(raw_desc).strip().lower() in ("nan", "none", ""))
                else str(raw_desc).strip()
            )

            job_location = str(row.get("location", "")).strip()
            job_id = _make_job_id(company, title, url)

            if job_id in seen_ids:
                continue
            seen_ids.add(job_id)

            all_jobs.append({
                "job_id":      job_id,
                "company":     company,
                "title":       title,
                "location":    job_location,
                "url":         url,
                "url_direct":  url_direct,
                "description": description,
                "date_found":  datetime.now().isoformat(),
                "source":      "jobspy",
            })
            count_new += 1

        log.info(f"  '{term}' → {count_new} jobs")

    log.info(f"Total jobs scraped: {len(all_jobs)}")
    return all_jobs
