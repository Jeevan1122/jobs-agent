CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT UNIQUE,
    company TEXT,
    title TEXT,
    location TEXT,
    url TEXT,
    date_found TEXT,
    date_applied TEXT,
    ats_score INTEGER,
    visa_flag TEXT,
    visa_note TEXT,
    status TEXT DEFAULT 'pending',
    resume_tex_path TEXT,
    resume_pdf_path TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS emails (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
CREATE INDEX IF NOT EXISTS idx_emails_status ON emails(status);
