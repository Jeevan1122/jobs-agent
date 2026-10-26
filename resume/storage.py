"""
Resume storage — Cloudflare R2 in production, local disk in development.

R2 is used when R2_ACCOUNT_ID + R2_ACCESS_KEY_ID + R2_SECRET_ACCESS_KEY are set.
Falls back to local disk (resume/users/{user_id}/uploaded_resume.pdf) otherwise.
"""

import io
import logging
import os

log = logging.getLogger(__name__)

_r2_client = None
_USE_R2 = bool(os.getenv("R2_ACCOUNT_ID") and os.getenv("R2_ACCESS_KEY_ID") and os.getenv("R2_SECRET_ACCESS_KEY"))


def _get_r2():
    global _r2_client
    if _r2_client is None:
        import boto3
        account_id = os.getenv("R2_ACCOUNT_ID")
        _r2_client = boto3.client(
            "s3",
            endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=os.getenv("R2_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY"),
            region_name="auto",
        )
    return _r2_client


def _bucket():
    return os.getenv("R2_BUCKET_NAME", "jobagent-resumes")


def _r2_key(user_id):
    return f"resumes/{user_id}/uploaded_resume.pdf"


def _local_path(user_id):
    base = os.path.join(os.path.dirname(os.path.dirname(__file__)), "resume", "users", str(user_id))
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "uploaded_resume.pdf")


# ── Public API ──────────────────────────────────────────────────────────────────

def save_resume(user_id, file_bytes: bytes) -> bool:
    """Save a PDF resume for a user. Returns True on success."""
    if _USE_R2:
        try:
            _get_r2().put_object(
                Bucket=_bucket(),
                Key=_r2_key(user_id),
                Body=file_bytes,
                ContentType="application/pdf",
            )
            log.info(f"Resume saved to R2 for user {user_id}")
            return True
        except Exception as e:
            log.error(f"R2 upload failed: {e} — falling back to local disk")

    path = _local_path(user_id)
    with open(path, "wb") as f:
        f.write(file_bytes)
    log.info(f"Resume saved to disk for user {user_id}: {path}")
    return True


def get_resume_bytes(user_id) -> bytes | None:
    """Return PDF bytes for the user's resume, or None if not found."""
    if _USE_R2:
        try:
            resp = _get_r2().get_object(Bucket=_bucket(), Key=_r2_key(user_id))
            return resp["Body"].read()
        except Exception as e:
            log.warning(f"R2 download failed for user {user_id}: {e}")
            return None

    path = _local_path(user_id)
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()
    return None


def get_resume_local_path(user_id) -> str | None:
    """
    Return a local file path for the resume (required by Playwright / pdfplumber).
    Downloads from R2 to a temp local file if using cloud storage.
    Returns None if no resume exists.
    """
    if _USE_R2:
        data = get_resume_bytes(user_id)
        if data is None:
            return None
        import tempfile
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf", prefix=f"resume_{user_id}_")
        tmp.write(data)
        tmp.close()
        return tmp.name

    path = _local_path(user_id)
    return path if os.path.exists(path) else None


def resume_exists(user_id) -> bool:
    """Return True if a resume has been uploaded for this user."""
    if _USE_R2:
        try:
            _get_r2().head_object(Bucket=_bucket(), Key=_r2_key(user_id))
            return True
        except Exception:
            return False
    return os.path.exists(_local_path(user_id))


def resume_metadata(user_id) -> dict | None:
    """Return size_kb and uploaded_at, or None if not found."""
    import datetime
    if _USE_R2:
        try:
            resp = _get_r2().head_object(Bucket=_bucket(), Key=_r2_key(user_id))
            size_kb = round(resp["ContentLength"] / 1024, 1)
            mtime = resp.get("LastModified")
            uploaded_at = mtime.strftime("%b %d, %Y at %I:%M %p") if mtime else "unknown"
            return {"size_kb": size_kb, "uploaded_at": uploaded_at}
        except Exception:
            return None

    path = _local_path(user_id)
    if not os.path.exists(path):
        return None
    size_kb = round(os.path.getsize(path) / 1024, 1)
    mtime = os.path.getmtime(path)
    uploaded_at = datetime.datetime.fromtimestamp(mtime).strftime("%b %d, %Y at %I:%M %p")
    return {"size_kb": size_kb, "uploaded_at": uploaded_at}
