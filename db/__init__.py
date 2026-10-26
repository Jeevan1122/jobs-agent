import os
import json
import sqlite3
from datetime import datetime
import config

DATABASE_URL = os.environ.get("DATABASE_URL")
USE_PG = bool(DATABASE_URL)
PH = "%s" if USE_PG else "?"


def _get_conn():
    if USE_PG:
        import psycopg2
        import psycopg2.extras
        # Railway appends ?sslmode=require — handle gracefully
        url = DATABASE_URL
        conn = psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor)
        conn.autocommit = False
        return conn
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _row(r):
    return dict(r) if r else None


def _is_duplicate(exc):
    msg = str(exc).lower()
    return "unique" in msg or "duplicate" in msg


def _q(sql):
    """Swap ? → %s for PostgreSQL."""
    return sql.replace("?", "%s") if USE_PG else sql


# ── Schema ─────────────────────────────────────────────────────────────────────

_SQLITE_SCHEMA = """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS profiles (
        user_id INTEGER PRIMARY KEY,
        phone TEXT,
        linkedin TEXT,
        github TEXT,
        visa_status TEXT DEFAULT 'F-1 STEM OPT',
        opt_expiry TEXT,
        work_authorization TEXT DEFAULT 'W-2 only',
        skills TEXT,
        job_titles TEXT,
        preferred_location TEXT,
        remote_preference TEXT DEFAULT 'any',
        FOREIGN KEY(user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS applications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        job_id TEXT,
        company TEXT,
        title TEXT,
        location TEXT,
        url TEXT,
        url_direct TEXT,
        date_found TEXT,
        date_applied TEXT,
        ats_score INTEGER,
        visa_flag TEXT,
        visa_note TEXT,
        matched_keywords TEXT,
        missing_required TEXT,
        notes TEXT,
        description TEXT,
        status TEXT DEFAULT 'pending',
        resume_tex_path TEXT,
        resume_pdf_path TEXT,
        submission_platform TEXT,
        account_email TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, job_id)
    );

    CREATE TABLE IF NOT EXISTS emails (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        gmail_message_id TEXT UNIQUE,
        gmail_thread_id TEXT,
        sender TEXT,
        subject TEXT,
        body_preview TEXT,
        category TEXT,
        company TEXT,
        role TEXT,
        draft_reply TEXT,
        status TEXT DEFAULT 'pending',
        reply_sent TEXT,
        received_at TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_applications_status ON applications(status);
    CREATE INDEX IF NOT EXISTS idx_applications_user   ON applications(user_id);
    CREATE INDEX IF NOT EXISTS idx_emails_status       ON emails(status);
    CREATE INDEX IF NOT EXISTS idx_emails_user         ON emails(user_id);
"""

_PG_TABLES = [
    """CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        email TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS profiles (
        user_id INTEGER PRIMARY KEY,
        phone TEXT,
        linkedin TEXT,
        github TEXT,
        visa_status TEXT DEFAULT 'F-1 STEM OPT',
        opt_expiry TEXT,
        work_authorization TEXT DEFAULT 'W-2 only',
        skills TEXT,
        job_titles TEXT,
        preferred_location TEXT,
        remote_preference TEXT DEFAULT 'any',
        FOREIGN KEY(user_id) REFERENCES users(id)
    )""",
    """CREATE TABLE IF NOT EXISTS applications (
        id SERIAL PRIMARY KEY,
        user_id INTEGER,
        job_id TEXT,
        company TEXT,
        title TEXT,
        location TEXT,
        url TEXT,
        url_direct TEXT,
        date_found TEXT,
        date_applied TEXT,
        ats_score INTEGER,
        visa_flag TEXT,
        visa_note TEXT,
        matched_keywords TEXT,
        missing_required TEXT,
        notes TEXT,
        description TEXT,
        status TEXT DEFAULT 'pending',
        resume_tex_path TEXT,
        resume_pdf_path TEXT,
        submission_platform TEXT,
        account_email TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, job_id)
    )""",
    """CREATE TABLE IF NOT EXISTS emails (
        id SERIAL PRIMARY KEY,
        user_id INTEGER,
        gmail_message_id TEXT UNIQUE,
        gmail_thread_id TEXT,
        sender TEXT,
        subject TEXT,
        body_preview TEXT,
        category TEXT,
        company TEXT,
        role TEXT,
        draft_reply TEXT,
        status TEXT DEFAULT 'pending',
        reply_sent TEXT,
        received_at TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""",
    "CREATE INDEX IF NOT EXISTS idx_applications_status ON applications(status)",
    "CREATE INDEX IF NOT EXISTS idx_applications_user   ON applications(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_emails_status       ON emails(status)",
    "CREATE INDEX IF NOT EXISTS idx_emails_user         ON emails(user_id)",
]


def init_db():
    conn = _get_conn()
    cur = conn.cursor()
    if USE_PG:
        for stmt in _PG_TABLES:
            cur.execute(stmt)
    else:
        cur.executescript(_SQLITE_SCHEMA)
    conn.commit()
    conn.close()


# ── User auth ──────────────────────────────────────────────────────────────────

def create_user(email, name, password_hash):
    conn = _get_conn()
    cur = conn.cursor()
    try:
        if USE_PG:
            cur.execute(
                "INSERT INTO users (email, name, password_hash) VALUES (%s, %s, %s) RETURNING id",
                (email.lower().strip(), name.strip(), password_hash),
            )
            uid = cur.fetchone()["id"]
        else:
            cur.execute(
                "INSERT INTO users (email, name, password_hash) VALUES (?, ?, ?)",
                (email.lower().strip(), name.strip(), password_hash),
            )
            uid = cur.lastrowid
        conn.commit()
        return uid
    except Exception as e:
        conn.rollback()
        if _is_duplicate(e):
            return None
        raise
    finally:
        conn.close()


def get_user_by_email(email):
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(_q("SELECT * FROM users WHERE email = ?"), (email.lower().strip(),))
    row = cur.fetchone()
    conn.close()
    return _row(row)


def get_user_by_id(user_id):
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(_q("SELECT * FROM users WHERE id = ?"), (user_id,))
    row = cur.fetchone()
    conn.close()
    return _row(row)


# ── Profile ────────────────────────────────────────────────────────────────────

def get_profile(user_id):
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(_q("SELECT * FROM profiles WHERE user_id = ?"), (user_id,))
    row = cur.fetchone()
    conn.close()
    return _row(row)


def save_profile(user_id, phone, linkedin, github, visa_status, opt_expiry,
                 work_authorization, skills, job_titles="", preferred_location="",
                 remote_preference="any"):
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(_q("""
        INSERT INTO profiles
            (user_id, phone, linkedin, github, visa_status, opt_expiry,
             work_authorization, skills, job_titles, preferred_location, remote_preference)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            phone=EXCLUDED.phone, linkedin=EXCLUDED.linkedin, github=EXCLUDED.github,
            visa_status=EXCLUDED.visa_status, opt_expiry=EXCLUDED.opt_expiry,
            work_authorization=EXCLUDED.work_authorization, skills=EXCLUDED.skills,
            job_titles=EXCLUDED.job_titles, preferred_location=EXCLUDED.preferred_location,
            remote_preference=EXCLUDED.remote_preference
    """), (user_id, phone, linkedin, github, visa_status, opt_expiry,
           work_authorization, skills, job_titles, preferred_location, remote_preference))
    conn.commit()
    conn.close()


# ── Jobs ───────────────────────────────────────────────────────────────────────

def job_exists(job_id, user_id=None):
    conn = _get_conn()
    cur = conn.cursor()
    if user_id:
        cur.execute(_q("SELECT 1 FROM applications WHERE job_id = ? AND user_id = ?"), (job_id, user_id))
    else:
        cur.execute(_q("SELECT 1 FROM applications WHERE job_id = ?"), (job_id,))
    result = cur.fetchone() is not None
    conn.close()
    return result


def save_job(job_dict, user_id=None):
    conn = _get_conn()
    cur = conn.cursor()
    matched = json.dumps(job_dict.get("matched_keywords", []))
    missing = json.dumps(job_dict.get("missing_required", []))
    try:
        cur.execute(_q("""
            INSERT INTO applications
                (user_id, job_id, company, title, location, url, url_direct, date_found, ats_score,
                 visa_flag, visa_note, matched_keywords, missing_required, notes, status, description)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """), (
            user_id,
            job_dict.get("job_id"),
            job_dict.get("company"),
            job_dict.get("title"),
            job_dict.get("location"),
            job_dict.get("url"),
            job_dict.get("url_direct", ""),
            job_dict.get("date_found", datetime.now().isoformat()),
            job_dict.get("ats_score"),
            job_dict.get("visa_flag", "ok"),
            job_dict.get("visa_note", ""),
            matched,
            missing,
            job_dict.get("notes", ""),
            job_dict.get("status", "pending"),
            job_dict.get("description", ""),
        ))
        conn.commit()
    except Exception as e:
        conn.rollback()
        if not _is_duplicate(e):
            raise
    finally:
        conn.close()


def update_job_status(job_id, status, resume_tex_path=None, resume_pdf_path=None,
                      submission_platform=None, account_email=None):
    conn = _get_conn()
    cur = conn.cursor()
    if status == "applied":
        cur.execute(_q("""
            UPDATE applications
            SET status=?, date_applied=?, resume_tex_path=?, resume_pdf_path=?,
                submission_platform=?, account_email=?
            WHERE job_id=?
        """), (status, datetime.now().isoformat(), resume_tex_path, resume_pdf_path,
               submission_platform, account_email, job_id))
    else:
        cur.execute(_q("UPDATE applications SET status=? WHERE job_id=?"), (status, job_id))
    conn.commit()
    conn.close()


def get_job_by_id(job_id):
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(_q("SELECT * FROM applications WHERE job_id = ?"), (job_id,))
    row = cur.fetchone()
    conn.close()
    return _row(row)


def get_all_jobs(user_id=None):
    conn = _get_conn()
    cur = conn.cursor()
    if user_id:
        cur.execute(_q("SELECT * FROM applications WHERE user_id = ? ORDER BY date_found DESC"), (user_id,))
    else:
        cur.execute("SELECT * FROM applications ORDER BY date_found DESC")
    rows = []
    for r in cur.fetchall():
        d = dict(r)
        for field in ("matched_keywords", "missing_required"):
            try:
                d[field] = json.loads(d.get(field) or "[]")
            except (json.JSONDecodeError, TypeError):
                d[field] = []
        rows.append(d)
    conn.close()
    return rows


# ── Emails ─────────────────────────────────────────────────────────────────────

def get_pending_replies(user_id=None):
    conn = _get_conn()
    cur = conn.cursor()
    if user_id:
        cur.execute(_q("SELECT * FROM emails WHERE status='pending' AND user_id=? ORDER BY received_at DESC"), (user_id,))
    else:
        cur.execute("SELECT * FROM emails WHERE status = 'pending' ORDER BY received_at DESC")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def save_email(email_dict, user_id=None):
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(_q("""
            INSERT INTO emails
                (user_id, gmail_message_id, gmail_thread_id, sender, subject, body_preview,
                 category, company, role, draft_reply, status, received_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """), (
            user_id,
            email_dict.get("gmail_message_id"),
            email_dict.get("gmail_thread_id"),
            email_dict.get("sender"),
            email_dict.get("subject"),
            email_dict.get("body_preview"),
            email_dict.get("category"),
            email_dict.get("company"),
            email_dict.get("role"),
            email_dict.get("draft_reply"),
            email_dict.get("status", "pending"),
            email_dict.get("received_at", datetime.now().isoformat()),
        ))
        conn.commit()
        if USE_PG:
            cur.execute("SELECT lastval()")
            last_id = cur.fetchone()["lastval"]
        else:
            last_id = cur.lastrowid
    except Exception as e:
        conn.rollback()
        last_id = None
        if not _is_duplicate(e):
            raise
    finally:
        conn.close()
    return last_id


def get_email_by_db_id(db_id):
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(_q("SELECT * FROM emails WHERE id = ?"), (db_id,))
    row = cur.fetchone()
    conn.close()
    return _row(row)


def update_email_status(email_id, status, reply_sent=None):
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        _q("UPDATE emails SET status = ?, reply_sent = ? WHERE id = ?"),
        (status, reply_sent, email_id),
    )
    conn.commit()
    conn.close()


def get_all_emails(user_id=None):
    conn = _get_conn()
    cur = conn.cursor()
    if user_id:
        cur.execute(_q("SELECT * FROM emails WHERE user_id=? ORDER BY received_at DESC"), (user_id,))
    else:
        cur.execute("SELECT * FROM emails ORDER BY received_at DESC")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


# ── Migrations (SQLite only — PG uses init_db) ─────────────────────────────────

def migrate_db():
    if USE_PG:
        _migrate_pg()
        return
    conn = _get_conn()
    cur = conn.cursor()
    new_cols = [
        ("applications", "matched_keywords", "TEXT"),
        ("applications", "missing_required", "TEXT"),
        ("applications", "notes", "TEXT"),
        ("applications", "description", "TEXT"),
        ("applications", "submission_platform", "TEXT"),
        ("applications", "account_email", "TEXT"),
        ("applications", "url_direct", "TEXT"),
        ("applications", "user_id", "INTEGER"),
        ("emails", "user_id", "INTEGER"),
        ("profiles", "job_titles", "TEXT"),
        ("profiles", "preferred_location", "TEXT"),
        ("profiles", "remote_preference", "TEXT"),
    ]
    for table, col, coltype in new_cols:
        try:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
        except Exception:
            pass
    conn.commit()
    conn.close()


def _migrate_pg():
    conn = _get_conn()
    cur = conn.cursor()
    new_cols = [
        ("applications", "matched_keywords", "TEXT"),
        ("applications", "missing_required", "TEXT"),
        ("applications", "notes", "TEXT"),
        ("applications", "description", "TEXT"),
        ("applications", "submission_platform", "TEXT"),
        ("applications", "account_email", "TEXT"),
        ("applications", "url_direct", "TEXT"),
        ("applications", "user_id", "INTEGER"),
        ("emails", "user_id", "INTEGER"),
        ("profiles", "job_titles", "TEXT"),
        ("profiles", "preferred_location", "TEXT"),
        ("profiles", "remote_preference", "TEXT"),
    ]
    for table, col, coltype in new_cols:
        try:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {coltype}")
            conn.commit()
        except Exception:
            conn.rollback()
    conn.close()
