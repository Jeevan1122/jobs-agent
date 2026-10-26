import base64
import logging
from email.mime.text import MIMEText

from gmail.watcher import get_gmail_service
import config

log = logging.getLogger(__name__)

CANDIDATE = config.CANDIDATE

SIGNATURE = f"""
Best regards,
{CANDIDATE['name']}
{CANDIDATE['phone']}
{CANDIDATE['email']}
linkedin.com/in/kodamati-jeevan-sai-4b5390195"""


def build_reply_body(draft_reply):
    return f"{draft_reply.strip()}\n{SIGNATURE}"


def send_reply(gmail_message_id, gmail_thread_id, to_email, subject, body, service=None):
    if service is None:
        service = get_gmail_service()

    full_body = build_reply_body(body)

    message = MIMEText(full_body, "plain")
    message["to"] = to_email
    message["subject"] = subject if subject.startswith("Re:") else f"Re: {subject}"

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")

    try:
        sent = service.users().messages().send(
            userId="me",
            body={"raw": raw, "threadId": gmail_thread_id},
        ).execute()
        log.info(f"Reply sent to {to_email} (message id: {sent.get('id')})")
        return {"success": True, "message_id": sent.get("id")}
    except Exception as e:
        log.error(f"Failed to send reply to {to_email}: {e}")
        return {"success": False, "error": str(e)}
