import hashlib
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests
from bs4 import BeautifulSoup

import config

log = logging.getLogger(__name__)

# Companies known to use Greenhouse — slug must match boards.greenhouse.io/{slug}
GREENHOUSE_COMPANIES = [
    # Fintech / Payments
    "stripe", "brex", "plaid", "affirm", "chime", "sofi", "robinhood",
    "coinbase", "ripple", "gemini", "kraken", "blockfi", "wealthfront",
    "betterment", "klarna", "marqeta", "nerdwallet",

    # Data / Analytics / Infrastructure
    "fivetran", "dbtlabs", "airbyte", "astronomer", "prefect", "dagster",
    "hightouch", "census-data", "rudderstack", "starburst", "dremio",
    "motherduck", "immuta", "alation", "atlan", "montecarlodata",
    "singlestore", "imply", "tinybird", "materialize",

    # Cloud / DevOps / Security
    "cloudflare", "fastly", "okta", "crowdstrike", "lacework",
    "snyk", "wiz", "orca-security", "aqua-security", "chainguard",
    "hashicorp", "replicated", "teleport", "strongdm",

    # Observability / Monitoring
    "datadog", "sentry", "honeycombio", "pagerduty", "incident-io",
    "grafana", "chronosphere", "lightstep",

    # Collaboration / Productivity
    "figma", "notion", "linear", "retool", "airtable", "miro",
    "loom", "coda", "clickup", "asana",

    # AI / ML
    "anthropic", "cohere", "huggingface", "scale-ai", "labelbox",
    "weights-and-biases", "determined-ai", "together-computer",
    "modal-labs", "baseten", "banana-dev",

    # Communication / CRM
    "twilio", "intercom", "hubspot", "zendesk", "freshworks",
    "front", "klaviyo", "customer-io", "braze", "iterable",

    # Databases / Backend
    "mongodb", "cockroachdb", "planetscale", "supabase", "neon",
    "elastic", "neo4j", "yugabyte", "influxdata",

    # E-commerce / Marketplace
    "doordash", "instacart", "grubhub", "gopuff", "faire",
    "whatnot", "stockx", "offerpad",

    # HR / Ops / Other SaaS
    "rippling", "deel", "remote", "lattice", "culture-amp",
    "leapsome", "workato", "tray", "merge", "findem",

    # Transportation / Mobility
    "lyft", "bird", "lime", "aurora", "cruise", "waymo",

    # Healthcare / BioTech
    "benchling", "tempus", "nuvation-bio", "devoted-health",
    "hims", "cerebral", "headspace",

    # Media / Content
    "buzzfeed", "vox-media", "substack",

    # Enterprise / Growth
    "block", "squarespace", "wix", "webflow", "netlify",
    "vercel", "render", "railway",
]


def _strip_html(html_text):
    if not html_text:
        return ""
    soup = BeautifulSoup(html_text, "lxml")
    return re.sub(r"\s+", " ", soup.get_text(separator=" ")).strip()


def _make_job_id(company_slug, gh_job_id):
    raw = f"greenhouse|{company_slug}|{gh_job_id}"
    return hashlib.md5(raw.encode()).hexdigest()


def _title_matches(title, search_terms):
    """True if the job title contains any of the user's search terms (loose match)."""
    title_lower = title.lower()
    for term in search_terms:
        # Match if the full term is in the title
        if term.lower() in title_lower:
            return True
        # Match if the most distinctive word (longest word > 4 chars) is in the title
        words = [w for w in term.lower().split() if len(w) > 4]
        if words and all(w in title_lower for w in words):
            return True
    return False


def _location_matches(job_location, preferred_location, remote_preference):
    loc_lower = (job_location or "").lower()
    if remote_preference == "remote":
        return "remote" in loc_lower
    if not preferred_location or preferred_location.lower() in ("united states", "usa", "us", ""):
        return True
    pref_lower = preferred_location.lower()
    return pref_lower in loc_lower or "remote" in loc_lower


def _fetch_company(company_slug, search_terms, preferred_location, remote_preference):
    url = f"https://boards-api.greenhouse.io/v1/boards/{company_slug}/jobs?content=true"
    try:
        resp = requests.get(url, timeout=8)
        if resp.status_code != 200:
            return []
        data = resp.json()
    except Exception as e:
        log.debug(f"Greenhouse {company_slug}: {e}")
        return []

    jobs = data.get("jobs", [])
    matched = []
    for job in jobs:
        title = job.get("title", "").strip()
        if not _title_matches(title, search_terms):
            continue

        loc_obj = job.get("location", {}) or {}
        job_location = loc_obj.get("name", "")

        if not _location_matches(job_location, preferred_location, remote_preference):
            continue

        gh_id = str(job.get("id", ""))
        apply_url = f"https://boards.greenhouse.io/{company_slug}/jobs/{gh_id}"
        description = _strip_html(job.get("content", ""))

        # Try to get a nicer company name from metadata
        company_name = (data.get("name") or company_slug.replace("-", " ").title())

        matched.append({
            "job_id": _make_job_id(company_slug, gh_id),
            "company": company_name,
            "title": title,
            "location": job_location,
            "url": apply_url,
            "url_direct": apply_url,
            "description": description,
            "date_found": datetime.now().isoformat(),
            "source": "greenhouse",
        })

    return matched


def scrape_greenhouse(search_terms=None, preferred_location=None, remote_preference="any"):
    """
    Scrape all GREENHOUSE_COMPANIES in parallel, filter by user's job titles + location.
    Returns list of job dicts compatible with the main pipeline.
    """
    terms = search_terms if search_terms else config.JOB_SEARCH["search_terms"]
    loc = preferred_location or ""
    remote = remote_preference or "any"

    log.info(f"Greenhouse: querying {len(GREENHOUSE_COMPANIES)} companies for {terms}")

    all_jobs = []
    seen_ids = set()

    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = {
            pool.submit(_fetch_company, slug, terms, loc, remote): slug
            for slug in GREENHOUSE_COMPANIES
        }
        for future in as_completed(futures):
            try:
                jobs = future.result()
                for job in jobs:
                    jid = job["job_id"]
                    if jid not in seen_ids:
                        seen_ids.add(jid)
                        all_jobs.append(job)
            except Exception as e:
                log.debug(f"Greenhouse future error: {e}")

    log.info(f"Greenhouse: {len(all_jobs)} matching jobs found across all companies")
    return all_jobs
