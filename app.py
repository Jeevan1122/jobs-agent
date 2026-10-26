import threading
import logging
import os
import sys
import smtplib
import datetime
import json
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from functools import wraps

from flask import Flask, jsonify, render_template, request, send_from_directory, redirect, url_for, session
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.security import generate_password_hash, check_password_hash
from authlib.integrations.flask_client import OAuth

sys.path.insert(0, os.path.dirname(__file__))

import db
import config
from scraper.linkedin_scraper import scrape_jobs
from scraper.greenhouse_scraper import scrape_greenhouse
from scraper.verify_jobs import filter_active
from screener.visa_ats_screen import screen_job
from resume.storage import save_resume, get_resume_local_path, resume_exists, resume_metadata

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "jobagent-secret-xk29zm")

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri=os.getenv("REDIS_URL", "memory://"),
)
log = logging.getLogger(__name__)

# ── OAuth ──────────────────────────────────────────────────────────────────────

oauth = OAuth(app)
oauth.register(
    name="google",
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)
oauth.register(
    name="github",
    client_id=os.getenv("GITHUB_CLIENT_ID"),
    client_secret=os.getenv("GITHUB_CLIENT_SECRET"),
    access_token_url="https://github.com/login/oauth/access_token",
    authorize_url="https://github.com/login/oauth/authorize",
    api_base_url="https://api.github.com/",
    client_kwargs={"scope": "user:email"},
)
oauth.register(
    name="facebook",
    client_id=os.getenv("FACEBOOK_APP_ID"),
    client_secret=os.getenv("FACEBOOK_APP_SECRET"),
    access_token_url="https://graph.facebook.com/oauth/access_token",
    authorize_url="https://www.facebook.com/dialog/oauth",
    api_base_url="https://graph.facebook.com/",
    client_kwargs={"scope": "email public_profile"},
)

# ── Auth helpers ───────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return decorated


def api_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user_id"):
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated


def _get_candidate(user_id):
    user    = db.get_user_by_id(user_id) or {}
    profile = db.get_profile(user_id) or {}
    return {
        "name":               user.get("name", ""),
        "email":              user.get("email", ""),
        "phone":              profile.get("phone", ""),
        "linkedin":           profile.get("linkedin", ""),
        "github":             profile.get("github", ""),
        "visa_status":        profile.get("visa_status", ""),
        "opt_expiry":         profile.get("opt_expiry", ""),
        "work_authorization": profile.get("work_authorization", ""),
    }




# ── Agent pipeline ─────────────────────────────────────────────────────────────

_agent_running: dict = {}   # user_id → bool
_submission_status = {}


def _run_pipeline(user_id):
    try:
        db.init_db()
        db.migrate_db()

        candidate  = _get_candidate(user_id)
        profile    = db.get_profile(user_id) or {}
        skills_text = profile.get("skills", "")

        # Per-user job search preferences
        raw_titles  = profile.get("job_titles") or ""
        user_terms  = [t.strip() for t in raw_titles.split(",") if t.strip()] or None
        user_loc    = profile.get("preferred_location") or None
        remote_pref = profile.get("remote_preference", "any")
        user_remote = True if remote_pref == "remote" else (False if remote_pref == "onsite" else None)

        # ── Scrape ────────────────────────────────────────────────────────────
        log.info("Scraping LinkedIn / Indeed / ZipRecruiter …")
        board_jobs = scrape_jobs(search_terms=user_terms, location=user_loc, remote=user_remote)

        log.info("Scraping Greenhouse boards …")
        gh_jobs = scrape_greenhouse(
            search_terms=user_terms,
            preferred_location=user_loc,
            remote_preference=remote_pref,
        )

        # Merge — deduplicate by job_id
        seen = {j["job_id"] for j in board_jobs}
        for j in gh_jobs:
            if j["job_id"] not in seen:
                board_jobs.append(j)
                seen.add(j["job_id"])

        # Skip jobs already in this user's DB
        new_jobs = [j for j in board_jobs if not db.job_exists(j["job_id"], user_id=user_id)]
        log.info(f"New jobs (not yet in DB): {len(new_jobs)}")

        if not new_jobs:
            log.info("No new jobs found this run.")
            return

        # ── Verify active ─────────────────────────────────────────────────────
        log.info(f"Verifying {len(new_jobs)} job URLs are still active …")
        active_jobs, expired_ids = filter_active(new_jobs)
        log.info(f"Active: {len(active_jobs)}  |  Expired/removed: {len(expired_ids)}")

        # ── Screen + save ─────────────────────────────────────────────────────
        saved_count = 0
        for job in active_jobs:
            screening = screen_job(job, candidate_skills=skills_text)
            job["ats_score"]         = screening["ats_score"]
            job["visa_flag"]         = screening["visa_flag"]
            job["visa_note"]         = screening["visa_note"]
            job["matched_keywords"]  = screening.get("matched_keywords", [])
            job["missing_required"]  = screening.get("missing_required", [])
            job["notes"]             = screening.get("notes", "")

            if screening["visa_flag"] == "hard_stop":
                job["status"] = "skipped"
            elif screening["ats_score"] < config.JOB_SEARCH["min_ats_score"]:
                job["status"] = "skipped"
            else:
                job["status"] = "pending"
                saved_count += 1

            db.save_job(job, user_id=user_id)

        log.info(f"Pipeline done — {saved_count} new jobs added to pending queue.")

    except Exception as e:
        log.error(f"Pipeline error: {e}", exc_info=True)
    finally:
        _agent_running[user_id] = False


# ── Submission ─────────────────────────────────────────────────────────────────

def _send_confirmation_email(candidate, job, result):
    gmail_pass = os.getenv("GMAIL_APP_PASSWORD")
    user_email = candidate.get("email")
    gmail_user = os.getenv("GMAIL_SMTP_USER") or user_email
    if not gmail_pass or not user_email:
        return
    submitted    = result.get("submitted", False)
    tailored     = result.get("tailored", False)
    submit_error = result.get("submit_error", "")
    app_url      = os.getenv("APP_URL", "http://localhost:5001")
    try:
        icon    = "✅" if submitted else "❌"
        subject = f"{icon} JobAgent {'Applied' if submitted else 'Failed'}: {job.get('company')} — {job.get('title')}"
        body    = f"""Hi {candidate.get('name', 'there')},

{'Your application was successfully submitted!' if submitted else 'The submission attempt failed — please apply manually.'}

━━━━━━━━━━━━━━━━━━━━━━━━
  Company:  {job.get('company', '—')}
  Role:     {job.get('title', '—')}
  Location: {job.get('location', '—')}
  Job URL:  {job.get('url', '—')}
━━━━━━━━━━━━━━━━━━━━━━━━

  Resume tailored:   {'✅ Yes' if tailored else '⚠️  No — JD unavailable'}
  Submission:        {'✅ Submitted via Playwright' if submitted else '❌ Failed — ' + str(submit_error)}

View your tracker: {app_url}

— JobAgent
"""
        msg = MIMEMultipart()
        msg["From"]    = gmail_user
        msg["To"]      = user_email
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_pass)
            server.send_message(msg)
        log.info(f"Confirmation email sent to {user_email}")
    except Exception as e:
        log.error(f"Email send failed: {e}")


def _run_submission(job_id, user_id):
    global _submission_status
    _submission_status[job_id] = {"status": "tailoring", "error": None}
    try:
        job = db.get_job_by_id(job_id)
        if not job:
            _submission_status[job_id] = {"status": "failed", "error": "Job not found"}
            return

        candidate      = _get_candidate(user_id)
        resume_pdf_path = get_resume_local_path(user_id)

        if not resume_pdf_path:
            _submission_status[job_id] = {
                "status": "failed",
                "error": "No resume uploaded. Go to Profile and upload your PDF resume first.",
            }
            db.update_job_status(job_id, "failed")
            return

        # Step 1 — Tailor resume
        from resume.tailor import tailor_resume
        jd_text = job.get("description") or ""

        def _parse_list(val):
            if isinstance(val, list):
                return val
            try:
                return json.loads(val or "[]")
            except Exception:
                return []

        ats_result = {
            "matched_keywords": _parse_list(job.get("matched_keywords")),
            "missing_required": _parse_list(job.get("missing_required")),
        }
        log.info(f"Tailoring resume for {job.get('company')} — {job.get('title')}")
        tailor_result = tailor_resume(
            jd_text=jd_text,
            job_title=job.get("title", ""),
            company=job.get("company", ""),
            ats_result=ats_result,
            pdf_path=resume_pdf_path,
        )
        tailored  = tailor_result.get("success", False)
        resume_pdf = tailor_result.get("pdf_path") or resume_pdf_path
        resume_tex = tailor_result.get("tex_path")

        if not tailored:
            log.warning(f"Tailoring failed ({tailor_result.get('error')}) — using uploaded PDF as-is")
            resume_pdf = resume_pdf_path

        # Step 2 — Submit
        _submission_status[job_id] = {"status": "submitting", "error": None}
        from submitter.playwright_submit import submit_application
        job_with_candidate = {**job, "_candidate": candidate}
        submit_result = submit_application(job_with_candidate, resume_pdf, dry_run=False)

        submitted    = submit_result.get("success", False)
        submit_error = submit_result.get("error")
        is_expired   = submit_result.get("expired", False)
        used_platform = submit_result.get("platform", "unknown")

        # Step 3 — Update DB
        if submitted:
            new_status = "applied"
        elif is_expired:
            new_status = "expired"
        else:
            new_status = "failed"

        db.update_job_status(
            job_id, new_status,
            resume_tex_path=resume_tex,
            resume_pdf_path=resume_pdf,
            submission_platform=used_platform,
            account_email=candidate.get("email", ""),
        )

        _submission_status[job_id] = {
            "status": "done" if submitted else new_status,
            "error":   submit_error,
            "tailored": tailored,
            "expired":  is_expired,
        }
        log.info(f"Submission {'✅' if submitted else '❌'} for {job.get('company')} ({new_status})")

        # Step 4 — Confirmation email
        _send_confirmation_email(candidate, job, {
            "submitted":    submitted,
            "tailored":     tailored,
            "submit_error": submit_error,
        })

    except Exception as e:
        log.error(f"Submission pipeline error for {job_id}: {e}", exc_info=True)
        _submission_status[job_id] = {"status": "failed", "error": str(e)}
        db.update_job_status(job_id, "failed")


# ── Auth routes ────────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login_page():
    if session.get("user_id"):
        return redirect(url_for("index"))

    error      = None
    reg_error  = None
    active_tab = request.args.get("tab", "signin")

    if request.method == "POST":
        action = request.form.get("action", "login")

        if action == "register":
            active_tab = "register"
            name     = request.form.get("name", "").strip()
            email    = request.form.get("email", "").strip()
            password = request.form.get("password", "")
            confirm  = request.form.get("confirm_password", "")

            if not name or not email or not password:
                reg_error = "All fields are required."
            elif len(password) < 8:
                reg_error = "Password must be at least 8 characters."
            elif password != confirm:
                reg_error = "Passwords do not match."
            else:
                user_id = db.create_user(email, name, generate_password_hash(password))
                if user_id is None:
                    reg_error = "An account with that email already exists."
                else:
                    session["user_id"]   = user_id
                    session["user_name"] = name
                    return redirect(url_for("profile_setup"))

        else:
            active_tab = "signin"
            email    = request.form.get("email", "").strip()
            password = request.form.get("password", "")
            user     = db.get_user_by_email(email)
            if user and check_password_hash(user["password_hash"], password):
                session["user_id"]   = user["id"]
                session["user_name"] = user["name"]
                profile = db.get_profile(user["id"])
                return redirect(url_for("index") if profile else url_for("profile_setup"))
            error = "Invalid email or password."

    err_param = request.args.get("error", "")
    if err_param and not error:
        error = {
            "google_not_configured": "Google login is not configured yet.",
            "github_not_configured": "GitHub login is not configured yet.",
            "facebook_not_configured": "Facebook login is not configured yet.",
            "google_failed":  "Google sign-in failed. Please try again.",
            "github_failed":  "GitHub sign-in failed. Please try again.",
            "github_no_email": "GitHub did not return an email address.",
            "facebook_failed": "Facebook sign-in failed. Please try again.",
        }.get(err_param, "Sign-in failed. Please try again.")

    return render_template("login.html",
        error=error, reg_error=reg_error, active_tab=active_tab,
        google_enabled=bool(os.getenv("GOOGLE_CLIENT_ID")),
        github_enabled=bool(os.getenv("GITHUB_CLIENT_ID")),
        facebook_enabled=bool(os.getenv("FACEBOOK_APP_ID")),
    )


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))


def _oauth_login_or_create(email, name):
    user = db.get_user_by_email(email)
    if not user:
        user_id = db.create_user(email, name, generate_password_hash(os.urandom(24).hex()))
        if not user_id:
            return redirect(url_for("login_page") + "?error=oauth_failed")
        session["user_id"]   = user_id
        session["user_name"] = name
        return redirect(url_for("profile_setup"))
    session["user_id"]   = user["id"]
    session["user_name"] = user["name"]
    profile = db.get_profile(user["id"])
    return redirect(url_for("index") if profile else url_for("profile_setup"))


@app.route("/auth/google")
def google_login():
    if not os.getenv("GOOGLE_CLIENT_ID"):
        return redirect(url_for("login_page") + "?error=google_not_configured")
    return oauth.google.authorize_redirect(url_for("google_callback", _external=True))


@app.route("/auth/google/callback")
def google_callback():
    try:
        token     = oauth.google.authorize_access_token()
        user_info = token.get("userinfo") or oauth.google.userinfo()
        return _oauth_login_or_create(user_info["email"], user_info.get("name", user_info["email"]))
    except Exception as e:
        log.error(f"Google OAuth error: {e}")
        return redirect(url_for("login_page") + "?error=google_failed")


@app.route("/auth/github")
def github_login():
    if not os.getenv("GITHUB_CLIENT_ID"):
        return redirect(url_for("login_page") + "?error=github_not_configured")
    return oauth.github.authorize_redirect(url_for("github_callback", _external=True))


@app.route("/auth/github/callback")
def github_callback():
    try:
        oauth.github.authorize_access_token()
        profile = oauth.github.get("user").json()
        email   = profile.get("email")
        if not email:
            primary = next((e for e in oauth.github.get("user/emails").json() if e.get("primary")), None)
            email   = primary["email"] if primary else None
        if not email:
            return redirect(url_for("login_page") + "?error=github_no_email")
        return _oauth_login_or_create(email, profile.get("name") or profile.get("login", email))
    except Exception as e:
        log.error(f"GitHub OAuth error: {e}")
        return redirect(url_for("login_page") + "?error=github_failed")


@app.route("/auth/facebook")
def facebook_login():
    if not os.getenv("FACEBOOK_APP_ID"):
        return redirect(url_for("login_page") + "?error=facebook_not_configured")
    return oauth.facebook.authorize_redirect(url_for("facebook_callback", _external=True))


@app.route("/auth/facebook/callback")
def facebook_callback():
    try:
        oauth.facebook.authorize_access_token()
        profile = oauth.facebook.get("me?fields=id,name,email").json()
        email   = profile.get("email")
        if not email:
            return redirect(url_for("login_page") + "?error=facebook_no_email")
        return _oauth_login_or_create(email, profile.get("name", email))
    except Exception as e:
        log.error(f"Facebook OAuth error: {e}")
        return redirect(url_for("login_page") + "?error=facebook_failed")


# ── Profile routes ─────────────────────────────────────────────────────────────

def _save_profile_from_form(user_id):
    db.save_profile(
        user_id=user_id,
        phone=request.form.get("phone", ""),
        linkedin=request.form.get("linkedin", ""),
        github=request.form.get("github", ""),
        visa_status=request.form.get("visa_status", "F-1 STEM OPT"),
        opt_expiry=request.form.get("opt_expiry", ""),
        work_authorization=request.form.get("work_authorization", ""),
        skills=request.form.get("skills", ""),
        job_titles=request.form.get("job_titles", ""),
        preferred_location=request.form.get("preferred_location", ""),
        remote_preference=request.form.get("remote_preference", "any"),
    )


@app.route("/profile/setup", methods=["GET", "POST"])
@login_required
def profile_setup():
    user_id = session["user_id"]
    profile = db.get_profile(user_id) or {}
    if request.method == "POST":
        _save_profile_from_form(user_id)
        return redirect(url_for("index"))
    has_resume = resume_exists(user_id)
    return render_template("profile.html", profile=profile, setup=True, error=None, has_resume=has_resume)


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile_edit():
    user_id = session["user_id"]
    profile = db.get_profile(user_id) or {}
    if request.method == "POST":
        _save_profile_from_form(user_id)
        profile = db.get_profile(user_id) or {}
    has_resume = resume_exists(user_id)
    return render_template("profile.html", profile=profile, setup=False, error=None, has_resume=has_resume)


# ── Dashboard ──────────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    return render_template("index.html", user_name=session.get("user_name", ""))


# ── Jobs API ───────────────────────────────────────────────────────────────────

def _auto_expire_old_jobs(pending_jobs):
    """
    Auto-skip pending jobs older than 48 hours — they are almost certainly expired.
    Returns list of jobs still considered fresh.
    """
    cutoff_hours = 48
    fresh = []
    for job in pending_jobs:
        date_str = job.get("date_found") or job.get("created_at") or ""
        try:
            found_at = datetime.datetime.fromisoformat(date_str)
            age_hours = (datetime.datetime.now() - found_at).total_seconds() / 3600
            if age_hours > cutoff_hours:
                db.update_job_status(job["job_id"], "skipped")
                log.info(f"Auto-skipped stale job ({age_hours:.0f}h old): {job.get('company')} — {job.get('title')}")
            else:
                fresh.append(job)
        except Exception:
            fresh.append(job)  # can't parse date → keep
    return fresh


@app.route("/api/jobs")
@api_login_required
def api_jobs():
    db.init_db()
    db.migrate_db()
    user_id = session["user_id"]
    all_jobs = db.get_all_jobs(user_id=user_id)

    pending = [j for j in all_jobs if j.get("status") == "pending"]

    # Step 1 — auto-skip stale jobs (> 48 h old), no HTTP needed
    pending = _auto_expire_old_jobs(pending)

    return jsonify(pending)


@app.route("/api/jobs/clean-expired", methods=["POST"])
@api_login_required
def clean_expired_jobs():
    """
    On-demand: HTTP-check all pending jobs and mark expired ones as skipped.
    Called from the "Clean Expired" button in the dashboard.
    """
    user_id  = session["user_id"]
    all_jobs = db.get_all_jobs(user_id=user_id)
    pending  = [j for j in all_jobs if j.get("status") == "pending"]

    if not pending:
        return jsonify({"removed": 0, "message": "No pending jobs to check."})

    active, expired_ids = filter_active(pending)
    for jid in expired_ids:
        db.update_job_status(jid, "skipped")

    log.info(f"Clean-expired: removed {len(expired_ids)} of {len(pending)} pending jobs.")
    return jsonify({"removed": len(expired_ids), "remaining": len(active)})


@app.route("/api/jobs/<job_id>/approve", methods=["POST"])
@api_login_required
def approve_job(job_id):
    user_id = session["user_id"]
    db.update_job_status(job_id, "approved")
    t = threading.Thread(target=_run_submission, args=(job_id, user_id), daemon=True)
    t.start()
    return jsonify({"success": True, "message": "Tailoring resume and submitting …"})


@app.route("/api/jobs/<job_id>/submission_status")
@api_login_required
def submission_status(job_id):
    status = _submission_status.get(job_id, {"status": "idle", "error": None})
    return jsonify(status)


@app.route("/api/jobs/<job_id>/skip", methods=["POST"])
@api_login_required
def skip_job(job_id):
    db.update_job_status(job_id, "skipped")
    return jsonify({"success": True})


# ── Email API ──────────────────────────────────────────────────────────────────

@app.route("/api/emails")
@api_login_required
def api_emails():
    db.init_db()
    emails = db.get_all_emails(user_id=session["user_id"])
    return jsonify(emails)


@app.route("/api/emails/<int:db_id>/approve", methods=["POST"])
@api_login_required
def approve_email(db_id):
    db.update_email_status(db_id, "approved")
    return jsonify({"success": True})


@app.route("/api/emails/<int:db_id>/skip", methods=["POST"])
@api_login_required
def skip_email(db_id):
    db.update_email_status(db_id, "skipped")
    return jsonify({"success": True})


# ── Agent control ──────────────────────────────────────────────────────────────

@app.route("/api/run", methods=["POST"])
@api_login_required
@limiter.limit("6 per hour")
def run_agent():
    user_id = session["user_id"]
    if _agent_running.get(user_id):
        return jsonify({"started": False, "reason": "Already running"})
    _agent_running[user_id] = True
    t = threading.Thread(target=_run_pipeline, args=(user_id,), daemon=True)
    t.start()
    return jsonify({"started": True})


@app.route("/api/status")
@api_login_required
def agent_status():
    return jsonify({"running": bool(_agent_running.get(session["user_id"]))})


# ── Tracker API ────────────────────────────────────────────────────────────────

@app.route("/api/tracker")
@api_login_required
def api_tracker():
    db.init_db()
    db.migrate_db()
    jobs  = db.get_all_jobs(user_id=session["user_id"])
    stats = {k: 0 for k in ["total", "applied", "approved", "pending", "skipped", "failed", "expired"]}
    stats["total"] = len(jobs)
    for j in jobs:
        s = j.get("status", "pending")
        if s in stats:
            stats[s] += 1
    applied = [j for j in jobs if j.get("status") in ("applied", "approved", "failed", "expired")]
    applied.sort(key=lambda j: j.get("date_applied") or j.get("date_found") or "", reverse=True)
    return jsonify({"stats": stats, "applications": applied})


@app.route("/api/tracker/<job_id>/details")
@api_login_required
def tracker_details(job_id):
    job = db.get_job_by_id(job_id)
    if not job:
        return jsonify({"error": "Not found"}), 404
    for field in ("matched_keywords", "missing_required"):
        try:
            job[field] = json.loads(job.get(field) or "[]")
        except Exception:
            job[field] = []
    candidate   = _get_candidate(session["user_id"])
    credentials = {
        "name":             candidate["name"],
        "email":            job.get("account_email") or candidate["email"],
        "phone":            candidate["phone"],
        "linkedin":         candidate["linkedin"],
        "github":           candidate["github"],
        "visa_status":      candidate["visa_status"],
        "opt_expiry":       candidate["opt_expiry"],
        "work_authorization": candidate["work_authorization"],
        "submitted_via":    job.get("submission_platform") or "—",
    }
    return jsonify({"job": job, "credentials": credentials})


@app.route("/api/tracker/<job_id>/resume")
@api_login_required
def tracker_resume(job_id):
    job = db.get_job_by_id(job_id)
    if not job:
        return jsonify({"error": "Not found"}), 404
    # Try job-specific tailored PDF first (local path from submission)
    pdf_path = job.get("resume_pdf_path")
    if pdf_path and os.path.exists(pdf_path):
        return send_from_directory(os.path.dirname(pdf_path), os.path.basename(pdf_path),
            as_attachment=True, download_name=f"{job.get('company','job')}_resume.pdf")
    # Fall back to user's uploaded resume
    from resume.storage import get_resume_bytes
    data = get_resume_bytes(session["user_id"])
    if data:
        from flask import Response
        company = job.get("company", "job")
        return Response(
            data,
            mimetype="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{company}_resume.pdf"'},
        )
    return jsonify({"error": "No resume file found"}), 404


# ── Resume API ─────────────────────────────────────────────────────────────────

@app.route("/api/resume/upload", methods=["POST"])
@api_login_required
def upload_resume():
    if "file" not in request.files:
        return jsonify({"success": False, "error": "No file provided"}), 400
    f = request.files["file"]
    if not f.filename.lower().endswith(".pdf"):
        return jsonify({"success": False, "error": "Only PDF files are accepted"}), 400
    user_id = session["user_id"]
    file_bytes = f.read()
    save_resume(user_id, file_bytes)
    size_kb = round(len(file_bytes) / 1024, 1)
    log.info(f"Resume uploaded by user {user_id}: {size_kb} KB")
    return jsonify({"success": True, "size_kb": size_kb, "filename": f.filename})


@app.route("/api/resume/status")
@api_login_required
def resume_status():
    meta = resume_metadata(session["user_id"])
    if meta:
        return jsonify({"exists": True, **meta})
    return jsonify({"exists": False})


@app.route("/api/resume/download")
@api_login_required
def download_resume():
    from resume.storage import get_resume_bytes
    user_id = session["user_id"]
    data = get_resume_bytes(user_id)
    if not data:
        return jsonify({"error": "No resume uploaded"}), 404
    user = db.get_user_by_id(user_id) or {}
    name = user.get("name", "Resume").replace(" ", "_")
    from flask import Response
    return Response(
        data,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{name}_Resume.pdf"'},
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    db.init_db()
    db.migrate_db()
    app.run(port=5001, debug=True)
