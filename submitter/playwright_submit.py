import logging
import os
import time
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

import config

log = logging.getLogger(__name__)

CANDIDATE = config.CANDIDATE

LINKEDIN_EMAIL   = os.getenv("LINKEDIN_EMAIL", "")
LINKEDIN_PASSWORD = os.getenv("LINKEDIN_PASSWORD", "")
INDEED_EMAIL     = os.getenv("INDEED_EMAIL", "")
INDEED_PASSWORD  = os.getenv("INDEED_PASSWORD", "")

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


# ── Platform detection ─────────────────────────────────────────────────────────

def detect_ats_platform(url):
    u = url.lower()
    if "greenhouse.io" in u or "boards.greenhouse" in u:  return "greenhouse"
    if "jobs.lever.co" in u or "lever.co" in u:           return "lever"
    if "myworkdayjobs.com" in u or "workday.com" in u:    return "workday"
    if "linkedin.com" in u:                                return "linkedin"
    if "ashbyhq.com" in u or "ashby.io" in u:             return "ashby"
    if "indeed.com" in u:                                  return "indeed"
    if "glassdoor.com" in u:                               return "glassdoor"
    if "smartrecruiters.com" in u:                         return "smartrecruiters"
    if "icims.com" in u:                                   return "icims"
    if "jobvite.com" in u:                                 return "jobvite"
    if "bamboohr.com" in u:                                return "bamboohr"
    if "ziprecruiter.com" in u:                            return "ziprecruiter"
    return "generic"


# ── Login helpers ──────────────────────────────────────────────────────────────

def _login_linkedin(page):
    if not LINKEDIN_EMAIL or not LINKEDIN_PASSWORD:
        log.warning("LinkedIn credentials not set in .env")
        return False
    try:
        page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=20000)
        page.fill("#username", LINKEDIN_EMAIL)
        page.fill("#password", LINKEDIN_PASSWORD)
        page.click("button[type='submit']")
        page.wait_for_timeout(4000)
        if "feed" in page.url or "mynetwork" in page.url or "jobs" in page.url:
            log.info("LinkedIn login successful")
            return True
        if "checkpoint" in page.url or "challenge" in page.url:
            log.warning("LinkedIn security check triggered — waiting 20s for manual action")
            page.wait_for_timeout(20000)
            return "feed" in page.url or "jobs" in page.url
        log.warning(f"LinkedIn login uncertain — URL: {page.url}")
        return False
    except Exception as e:
        log.error(f"LinkedIn login error: {e}")
        return False


def _login_indeed(page):
    if not INDEED_EMAIL or not INDEED_PASSWORD:
        log.warning("Indeed credentials not set in .env")
        return False
    try:
        page.goto("https://secure.indeed.com/auth", wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(2000)
        email_input = page.query_selector("input[type='email'], input[name='email'], #ifl-InputFormField-3")
        if email_input:
            email_input.fill(INDEED_EMAIL)
            page.keyboard.press("Enter")
            page.wait_for_timeout(2000)
        pass_input = page.query_selector("input[type='password']")
        if pass_input:
            pass_input.fill(INDEED_PASSWORD)
            page.keyboard.press("Enter")
            page.wait_for_timeout(3000)
        if "indeed.com" in page.url and "auth" not in page.url:
            log.info("Indeed login successful")
            return True
        log.warning(f"Indeed login uncertain — URL: {page.url}")
        return False
    except Exception as e:
        log.error(f"Indeed login error: {e}")
        return False


# ── Form fill helpers ──────────────────────────────────────────────────────────

def _fill(page, selector, value, timeout=5000):
    try:
        page.wait_for_selector(selector, timeout=timeout)
        page.fill(selector, value)
        return True
    except PlaywrightTimeout:
        return False


def _upload_resume(page, resume_pdf_path, timeout=8000):
    try:
        inp = page.query_selector("input[type='file']")
        if inp and resume_pdf_path and os.path.exists(resume_pdf_path):
            inp.set_input_files(resume_pdf_path)
            page.wait_for_timeout(1500)
            return True
    except Exception as e:
        log.warning(f"Resume upload failed: {e}")
    return False


def _answer_work_auth(page):
    """Answer common work authorization yes/no questions."""
    selectors_yes = [
        "select[id*='authorized']", "select[id*='eligible']", "select[name*='authorized']",
    ]
    selectors_no = [
        "select[id*='sponsor']", "select[name*='sponsor']",
    ]
    for sel in selectors_yes:
        try:
            el = page.query_selector(sel)
            if el:
                el.select_option(label="Yes")
        except Exception:
            pass
    for sel in selectors_no:
        try:
            el = page.query_selector(sel)
            if el:
                el.select_option(label="No")
        except Exception:
            pass


# ── ATS-specific submitters ────────────────────────────────────────────────────

def _get_candidate(job):
    """Return per-user candidate info from job dict, falling back to config."""
    return job.get("_candidate") or CANDIDATE


def _submit_greenhouse(page, job, pdf):
    c = _get_candidate(job)
    first = c["name"].split()[0]
    last  = " ".join(c["name"].split()[1:]) if len(c["name"].split()) > 1 else c["name"]
    try:
        # Wait for the application form to load
        page.wait_for_selector(
            "form#application-form, form.application, div#application-form, #app",
            timeout=20000,
        )
        page.wait_for_timeout(2000)

        # Fill name — try multiple selectors Greenhouse uses
        for sel in ["input#first_name", "input[name='job_application[first_name]']",
                    "input[autocomplete='given-name']"]:
            if _fill(page, sel, first, timeout=3000):
                break

        for sel in ["input#last_name", "input[name='job_application[last_name]']",
                    "input[autocomplete='family-name']"]:
            if _fill(page, sel, last, timeout=3000):
                break

        for sel in ["input#email", "input[name='job_application[email]']",
                    "input[type='email']"]:
            if _fill(page, sel, c["email"], timeout=3000):
                break

        for sel in ["input#phone", "input[name='job_application[phone]']",
                    "input[type='tel']"]:
            if _fill(page, sel, c.get("phone", ""), timeout=3000):
                break

        # LinkedIn
        for sel in ["input#job_application_linkedin_profile_url",
                    "input[name*='linkedin']", "input[placeholder*='LinkedIn']"]:
            if _fill(page, sel, c.get("linkedin", ""), timeout=2000):
                break

        # Upload resume
        _upload_resume(page, pdf)
        page.wait_for_timeout(1500)

        # Answer work authorization dropdowns/radios
        _answer_work_auth(page)

        # Submit
        for sel in ["button#submit_app", "button[type='submit']", "input[type='submit']",
                    "button:has-text('Submit')", "button:has-text('Apply')"]:
            try:
                btn = page.query_selector(sel)
                if btn and btn.is_visible():
                    btn.click()
                    page.wait_for_timeout(4000)
                    break
            except Exception:
                pass

        # Verify success — Greenhouse shows a confirmation page or message
        page_text = page.inner_text("body")[:500].lower()
        if any(w in page_text for w in ["thank you", "application received",
                                         "successfully submitted", "application submitted"]):
            log.info(f"Greenhouse confirmed for {job.get('company')}")
            return {"success": True, "platform": "greenhouse"}

        log.info(f"Greenhouse submitted for {job.get('company')} (no confirm text found)")
        return {"success": True, "platform": "greenhouse"}

    except Exception as e:
        log.error(f"Greenhouse submission error: {e}")
        return {"success": False, "platform": "greenhouse", "error": str(e)}


def _submit_lever(page, job, pdf):
    c = _get_candidate(job)
    try:
        page.wait_for_selector("form.application-form, form[data-qa='apply-form']", timeout=15000)
        _fill(page, "input[name='name']", c["name"])
        _fill(page, "input[name='email']", c["email"])
        _fill(page, "input[name='phone']", c.get("phone", ""))
        _fill(page, "input[name='urls[LinkedIn]'], input[placeholder*='LinkedIn']", c.get("linkedin", ""))
        _upload_resume(page, pdf)
        page.click("button[type='submit']")
        page.wait_for_timeout(3000)
        log.info(f"Lever submission done for {job.get('company')}")
        return {"success": True, "platform": "lever"}
    except Exception as e:
        return {"success": False, "platform": "lever", "error": str(e)}


def _submit_ashby(page, job, pdf):
    c = _get_candidate(job)
    try:
        page.wait_for_selector("form", timeout=15000)
        _fill(page, "input[name='_systemfield_name']", c["name"])
        _fill(page, "input[name='_systemfield_email']", c["email"])
        _fill(page, "input[name='_systemfield_phone']", c.get("phone", ""))
        _upload_resume(page, pdf)
        page.click("button[type='submit']")
        page.wait_for_timeout(3000)
        return {"success": True, "platform": "ashby"}
    except Exception as e:
        return {"success": False, "platform": "ashby", "error": str(e)}


def _submit_smartrecruiters(page, job, pdf):
    c = _get_candidate(job)
    try:
        page.wait_for_selector("button[data-hook='btn-apply-top'], a[data-hook='btn-apply']", timeout=10000)
        page.click("button[data-hook='btn-apply-top'], a[data-hook='btn-apply']")
        page.wait_for_timeout(2000)
        _fill(page, "input[name='firstName']", c["name"].split()[0])
        _fill(page, "input[name='lastName']", " ".join(c["name"].split()[1:]))
        _fill(page, "input[name='email']", c["email"])
        _fill(page, "input[name='phoneNumber']", c.get("phone", ""))
        _upload_resume(page, pdf)
        page.click("button[data-hook='navigation-next'], button[type='submit']")
        page.wait_for_timeout(3000)
        return {"success": True, "platform": "smartrecruiters"}
    except Exception as e:
        return {"success": False, "platform": "smartrecruiters", "error": str(e)}


def _submit_linkedin_easy_apply(page, job, pdf):
    try:
        page.wait_for_timeout(2000)
        easy_btn = page.query_selector("button.jobs-apply-button, button[aria-label*='Easy Apply']")
        if not easy_btn:
            return {"success": False, "platform": "linkedin", "error": "Easy Apply button not found — job may not support Easy Apply"}
        easy_btn.click()
        page.wait_for_timeout(2000)

        _upload_resume(page, pdf)

        for _ in range(15):
            page.wait_for_timeout(1500)
            submit = page.query_selector("button[aria-label='Submit application']")
            review = page.query_selector("button[aria-label='Review your application']")
            next_btn = page.query_selector("button[aria-label='Continue to next step']")
            unfollow = page.query_selector("button[aria-label='Unfollow']")

            if submit:
                submit.click()
                page.wait_for_timeout(2000)
                return {"success": True, "platform": "linkedin_easy_apply"}
            elif unfollow:
                unfollow.click()
            elif review:
                review.click()
            elif next_btn:
                next_btn.click()
            else:
                break

        return {"success": False, "platform": "linkedin", "error": "Could not reach Submit button in Easy Apply flow"}
    except Exception as e:
        return {"success": False, "platform": "linkedin", "error": str(e)}


def _submit_indeed_apply(page, job, pdf):
    """Handle Indeed Apply flow after being logged in."""
    c = _get_candidate(job)
    try:
        # Click the apply button on the Indeed job page
        apply_btn = page.query_selector("button[id*='apply'], a[id*='apply'], button:has-text('Apply now'), button:has-text('Apply')")
        if apply_btn:
            apply_btn.click()
            page.wait_for_timeout(3000)

        # Step through Indeed Apply multi-step form
        for _ in range(10):
            page.wait_for_timeout(2000)

            # Upload resume if prompted
            _upload_resume(page, pdf)

            # Fill basic fields if visible
            _fill(page, "input[name*='name'][aria-label*='First']", c["name"].split()[0], timeout=2000)
            _fill(page, "input[name*='name'][aria-label*='Last']", " ".join(c["name"].split()[1:]), timeout=2000)
            _fill(page, "input[type='tel']", c.get("phone", ""), timeout=2000)

            # Work auth questions
            for radio in page.query_selector_all("input[type='radio']"):
                try:
                    label = page.query_selector(f"label[for='{radio.get_attribute('id')}']")
                    if label:
                        text = label.inner_text().lower()
                        if any(w in text for w in ["yes", "authorized", "eligible"]):
                            radio.click()
                        elif any(w in text for w in ["no", "not require", "don't need"]):
                            radio.click()
                except Exception:
                    pass

            continue_btn = page.query_selector("button[type='submit']:has-text('Continue'), button:has-text('Continue'), button:has-text('Next')")
            submit_btn = page.query_selector("button:has-text('Submit'), button[aria-label*='Submit']")

            if submit_btn:
                submit_btn.click()
                page.wait_for_timeout(2000)
                return {"success": True, "platform": "indeed_apply"}
            elif continue_btn:
                continue_btn.click()
            else:
                break

        return {"success": False, "platform": "indeed", "error": "Could not complete Indeed Apply flow"}
    except Exception as e:
        return {"success": False, "platform": "indeed", "error": str(e)}


# ── Extract real ATS URL from a job listing page ──────────────────────────────

def _extract_direct_url(page):
    """
    After landing on an Indeed/LinkedIn/ZipRecruiter listing, find and return
    the actual company ATS URL without clicking (to avoid popups/modals).
    """
    # Indeed: the external apply link is often in a data attribute or meta tag
    for sel in [
        "a[href*='greenhouse.io']", "a[href*='lever.co']", "a[href*='ashbyhq.com']",
        "a[href*='workday']", "a[href*='smartrecruiters']", "a[href*='jobvite']",
        "a[href*='bamboohr']", "a[href*='icims']", "a[href*='taleo']",
        "a[href*='myworkdayjobs']",
    ]:
        try:
            el = page.query_selector(sel)
            if el:
                href = el.get_attribute("href")
                if href:
                    log.info(f"Found direct ATS link in page: {href[:80]}")
                    return href
        except Exception:
            pass

    # Check meta refresh or canonical link
    try:
        content = page.content()
        import re
        for pattern in [
            r'href="(https://[^"]*greenhouse\.io[^"]*)"',
            r'href="(https://[^"]*lever\.co[^"]*)"',
            r'href="(https://[^"]*ashbyhq\.com[^"]*)"',
            r'href="(https://[^"]*myworkdayjobs\.com[^"]*)"',
            r'href="(https://[^"]*smartrecruiters\.com[^"]*)"',
        ]:
            m = re.search(pattern, content)
            if m:
                url = m.group(1)
                log.info(f"Found ATS URL in page HTML: {url[:80]}")
                return url
    except Exception:
        pass

    return None


# ── Apply-button follower ──────────────────────────────────────────────────────

def _follow_apply_redirect(page, job, pdf):
    """Click the Apply button on a job listing and submit on wherever it lands."""
    selectors = [
        "a[href*='apply']", "button:has-text('Apply Now')", "button:has-text('Apply')",
        "a:has-text('Apply Now')", "a:has-text('Apply')", "[data-testid*='apply']",
        ".apply-button", "#apply-button",
    ]
    for sel in selectors:
        try:
            el = page.query_selector(sel)
            if not el:
                continue
            href = el.get_attribute("href") or ""
            if href and href.startswith("http"):
                # External link — navigate directly
                page.goto(href, wait_until="domcontentloaded", timeout=20000)
            else:
                with page.expect_navigation(timeout=15000, wait_until="domcontentloaded"):
                    el.click()
            new_url = page.url
            new_platform = detect_ats_platform(new_url)
            log.info(f"Followed Apply → {new_url[:80]} ({new_platform})")
            return _dispatch_direct(page, new_platform, job, pdf, new_url)
        except Exception as e:
            log.debug(f"Apply selector {sel} failed: {e}")
            continue
    return {"success": False, "error": "No Apply button found on job listing page — apply manually", "platform": "unknown"}


def _dispatch_direct(page, platform, job, pdf, url):
    if platform == "greenhouse":        return _submit_greenhouse(page, job, pdf)
    if platform == "lever":             return _submit_lever(page, job, pdf)
    if platform == "ashby":             return _submit_ashby(page, job, pdf)
    if platform == "smartrecruiters":   return _submit_smartrecruiters(page, job, pdf)
    if platform == "linkedin":          return _submit_linkedin_easy_apply(page, job, pdf)
    if platform == "indeed":            return _submit_indeed_apply(page, job, pdf)
    if platform in ("workday", "icims", "jobvite", "bamboohr"):
        return {"success": False, "platform": platform,
                "error": f"{platform.title()} requires account creation — apply manually at: {url}"}
    return {"success": False, "platform": "unknown", "error": f"Unsupported ATS at {url[:80]}"}


# ── Main entry point ───────────────────────────────────────────────────────────

EXPIRED_PHRASES = [
    "no longer accepting", "job posting has expired", "this job has expired",
    "position has been filled", "no longer available", "posting has been removed",
    "job is no longer", "this position is no longer", "application period has closed",
    "not currently accepting",
]


def _is_expired(page):
    try:
        text = page.inner_text("body").lower()
        return any(p in text for p in EXPIRED_PHRASES)
    except Exception:
        return False


def submit_application(job_dict, resume_pdf_path, dry_run=True):
    # Prefer the direct company ATS URL over the job board listing URL
    url_direct = job_dict.get("url_direct", "")
    url_listing = job_dict.get("url", "")
    url = url_direct if url_direct else url_listing
    platform = detect_ats_platform(url)
    company = job_dict.get("company", "")
    log.info(f"Submitting: {company} | platform={platform} | url={url[:80]}")

    if dry_run:
        return {"success": True, "platform": platform, "dry_run": True}

    try:
        with sync_playwright() as p:
            headless = os.getenv("PLAYWRIGHT_HEADLESS", "true").lower() != "false"
            browser = p.chromium.launch(
                headless=headless,
                args=["--no-sandbox", "--disable-setuid-sandbox"],
            )
            context = browser.new_context(user_agent=UA)
            page = context.new_page()

            # ── Quick expiry check on listing URL ─────────────────────────────
            check_url = url_listing if url_listing else url
            if check_url and platform not in ("greenhouse", "lever", "ashby"):
                try:
                    page.goto(check_url, wait_until="domcontentloaded", timeout=15000)
                    if _is_expired(page):
                        browser.close()
                        log.info(f"Job expired: {company}")
                        return {"success": False, "platform": platform, "expired": True,
                                "error": "Job posting has expired and is no longer accepting applications."}
                except Exception:
                    pass

            # ── LinkedIn flow ──────────────────────────────────────────────────
            if platform == "linkedin":
                logged_in = _login_linkedin(page)
                if not logged_in:
                    browser.close()
                    return {"success": False, "platform": "linkedin",
                            "error": "LinkedIn login failed — check LINKEDIN_EMAIL / LINKEDIN_PASSWORD in .env"}
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(2000)
                result = _submit_linkedin_easy_apply(page, job_dict, resume_pdf_path)
                if not result["success"]:
                    # Not Easy Apply — follow external apply link
                    result = _follow_apply_redirect(page, job_dict, resume_pdf_path)

            # ── Indeed flow ────────────────────────────────────────────────────
            elif platform == "indeed":
                logged_in = _login_indeed(page)
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(2000)
                # First: try to extract direct ATS URL from page HTML
                direct = _extract_direct_url(page)
                if direct:
                    new_platform = detect_ats_platform(direct)
                    page.goto(direct, wait_until="domcontentloaded", timeout=20000)
                    result = _dispatch_direct(page, new_platform, job_dict, resume_pdf_path, direct)
                else:
                    result = _submit_indeed_apply(page, job_dict, resume_pdf_path)
                    if not result["success"]:
                        result = _follow_apply_redirect(page, job_dict, resume_pdf_path)

            # ── Glassdoor — follow Apply button to real ATS ────────────────────
            elif platform == "glassdoor":
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(2000)
                result = _follow_apply_redirect(page, job_dict, resume_pdf_path)

            # ── Direct ATS URLs (Greenhouse, Lever, Ashby, etc.) ──────────────
            elif platform in ("greenhouse", "lever", "ashby", "smartrecruiters"):
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
                result = _dispatch_direct(page, platform, job_dict, resume_pdf_path, url)

            # ── Unknown/generic — extract or follow apply ──────────────────────
            else:
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(2000)
                # Try extracting a known ATS link from page HTML first
                direct = _extract_direct_url(page)
                if direct:
                    new_platform = detect_ats_platform(direct)
                    page.goto(direct, wait_until="domcontentloaded", timeout=20000)
                    result = _dispatch_direct(page, new_platform, job_dict, resume_pdf_path, direct)
                else:
                    result = _dispatch_direct(page, platform, job_dict, resume_pdf_path, url)
                    if not result.get("success"):
                        result = _follow_apply_redirect(page, job_dict, resume_pdf_path)

            browser.close()
            return result

    except Exception as e:
        log.error(f"Submission error for {company}: {e}")
        return {"success": False, "platform": platform, "error": str(e)}
