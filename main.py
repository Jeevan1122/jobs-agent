import argparse
import logging
import os

import db
from scraper.linkedin_scraper import scrape_jobs
from screener.visa_ats_screen import screen_job
from resume.tailor import tailor_resume
from gmail.watcher import fetch_unread_recruiter_emails, mark_as_read, get_gmail_service
from gmail.classifier import classify_email
import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger(__name__)

DRY_RUN = False


def run_job_pipeline():
    log.info("=== JOB PIPELINE START ===")
    jobs = scrape_jobs()

    if not jobs:
        log.info("No new jobs found today.")
        return

    for job in jobs:
        log.info(f"Screening: {job.get('company')} — {job.get('title')}")
        screening = screen_job(job)

        job["ats_score"] = screening["ats_score"]
        job["visa_flag"] = screening["visa_flag"]
        job["visa_note"] = screening["visa_note"]
        job["matched_keywords"] = screening.get("matched_keywords", [])
        job["missing_required"] = screening.get("missing_required", [])
        job["notes"] = screening.get("notes", "")

        if screening["visa_flag"] == "hard_stop":
            log.info(f"  HARD STOP — {screening['visa_note']}")
            job["status"] = "skipped"
            db.save_job(job)
            continue

        if screening["ats_score"] < config.JOB_SEARCH["min_ats_score"]:
            log.info(f"  ATS too low ({screening['ats_score']}%) — skipping")
            job["status"] = "skipped"
            db.save_job(job)
            continue

        job["status"] = "pending"
        db.save_job(job)

        log.info(f"  ATS {screening['ats_score']}% | Visa {screening['visa_flag']} — queuing for approval")

        tailor_result = {"success": False, "tex_path": None, "pdf_path": None}
        if not DRY_RUN:
            tailor_result = tailor_resume(
                jd_text=job.get("description", ""),
                job_title=job.get("title", ""),
                company=job.get("company", ""),
                ats_result=screening,
            )

        if tailor_result["success"]:
            db.update_job_status(
                job["job_id"],
                "pending",
                resume_tex_path=tailor_result["tex_path"],
                resume_pdf_path=tailor_result.get("pdf_path"),
            )

        try:
            import asyncio
            from approval.telegram_bot import send_job_approval_request
            asyncio.run(send_job_approval_request(job, screening, tailor_result.get("tex_path")))
        except Exception as e:
            log.error(f"  Telegram notification failed: {e}")

    log.info("=== JOB PIPELINE END ===")


def run_gmail_pipeline():
    log.info("=== GMAIL PIPELINE START ===")

    try:
        gmail_service = get_gmail_service()
    except Exception as e:
        log.error(f"Gmail auth failed: {e}")
        return

    emails = fetch_unread_recruiter_emails(service=gmail_service)
    log.info(f"Fetched {len(emails)} unread recruiter emails.")

    for email in emails:
        log.info(f"  Classifying: {email.get('subject')} from {email.get('sender')}")
        classification = classify_email(email)

        category = classification.get("category", "unknown")
        log.info(f"  Category: {category} | Company: {classification.get('company')}")

        email_record = {
            **email,
            "category": category,
            "company": classification.get("company"),
            "role": classification.get("role"),
            "draft_reply": classification.get("draft_reply"),
            "status": "pending",
        }

        db_id = db.save_email(email_record)
        mark_as_read(gmail_service, email["gmail_message_id"])

        if category == "rejection":
            log.info("  Rejection — logging, no notification.")
            db.update_email_status(db_id, "skipped")
            continue

        if not classification.get("should_notify", True):
            continue

        if DRY_RUN:
            log.info(f"  [DRY RUN] Would send Telegram approval for {category}")
            continue

        if category == "offer":
            log.warning(f"  OFFER RECEIVED from {email.get('sender')} — sending URGENT alert")

        try:
            import asyncio
            from approval.telegram_bot import send_email_approval_request
            asyncio.run(send_email_approval_request(email, classification, db_id))
        except Exception as e:
            log.error(f"  Telegram notification failed: {e}")

    log.info("=== GMAIL PIPELINE END ===")


def main():
    global DRY_RUN

    parser = argparse.ArgumentParser(description="Job Agent — automated job search and Gmail reply manager")
    parser.add_argument("--dry-run", action="store_true", help="Run without submitting applications or sending emails")
    parser.add_argument("--jobs-only", action="store_true", help="Run only the job scraping pipeline")
    parser.add_argument("--gmail-only", action="store_true", help="Run only the Gmail pipeline")
    parser.add_argument("--bot-only", action="store_true", help="Run only the Telegram approval bot")
    args = parser.parse_args()

    DRY_RUN = args.dry_run
    if DRY_RUN:
        log.info("*** DRY RUN MODE — no applications submitted, no emails sent ***")

    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    db.init_db()

    if args.bot_only:
        from approval.telegram_bot import run_bot
        run_bot()
        return

    if args.jobs_only:
        run_job_pipeline()
        return

    if args.gmail_only:
        run_gmail_pipeline()
        return

    run_job_pipeline()
    run_gmail_pipeline()

    log.info("Pipelines complete. Starting Telegram bot for approvals...")
    from approval.telegram_bot import run_bot
    run_bot()


if __name__ == "__main__":
    main()
