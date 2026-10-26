import base64
import logging
import os
from datetime import datetime

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

import config

log = logging.getLogger(__name__)

SEARCH_QUERY = (
    "is:unread "
    "(opportunity OR position OR role OR interview OR screening OR offer OR recruiter) "
    "-from:me"
)


def get_gmail_service():
    creds = None

    if os.path.exists(config.GMAIL_TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(config.GMAIL_TOKEN_PATH, config.GMAIL_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                config.GMAIL_CREDENTIALS_PATH, config.GMAIL_SCOPES
            )
            creds = flow.run_local_server(port=0)
        with open(config.GMAIL_TOKEN_PATH, "w") as token_file:
            token_file.write(creds.to_json())

    service = build("gmail", "v1", credentials=creds)
    log.info("Gmail service authenticated successfully.")
    return service


def _decode_body(payload):
    """Extract plain text body from Gmail message payload."""
    body = ""
    if "parts" in payload:
        for part in payload["parts"]:
            if part.get("mimeType") == "text/plain":
                data = part.get("body", {}).get("data", "")
                if data:
                    body = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
                    break
        if not body:
            for part in payload["parts"]:
                if part.get("mimeType") == "text/html":
                    data = part.get("body", {}).get("data", "")
                    if data:
                        body = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
                        break
    else:
        data = payload.get("body", {}).get("data", "")
        if data:
            body = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
    return body


def _get_header(headers, name):
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def fetch_unread_recruiter_emails(service=None):
    if service is None:
        service = get_gmail_service()

    emails = []
    try:
        response = service.users().messages().list(
            userId="me",
            q=SEARCH_QUERY,
            maxResults=50,
        ).execute()

        messages = response.get("messages", [])
        log.info(f"Found {len(messages)} unread recruiter emails.")

        for msg_ref in messages:
            msg_id = msg_ref["id"]
            try:
                msg = service.users().messages().get(
                    userId="me", id=msg_id, format="full"
                ).execute()

                headers = msg.get("payload", {}).get("headers", [])
                sender = _get_header(headers, "From")
                subject = _get_header(headers, "Subject")
                date_str = _get_header(headers, "Date")
                thread_id = msg.get("threadId", "")

                body = _decode_body(msg.get("payload", {}))
                body_preview = body[:500].replace("\n", " ").strip()

                emails.append({
                    "gmail_message_id": msg_id,
                    "gmail_thread_id": thread_id,
                    "sender": sender,
                    "subject": subject,
                    "body_preview": body_preview,
                    "received_at": date_str or datetime.now().isoformat(),
                })
            except Exception as e:
                log.warning(f"Could not fetch message {msg_id}: {e}")

    except Exception as e:
        log.error(f"Gmail fetch failed: {e}")

    return emails


def mark_as_read(service, message_id):
    try:
        service.users().messages().modify(
            userId="me",
            id=message_id,
            body={"removeLabelIds": ["UNREAD"]},
        ).execute()
    except Exception as e:
        log.warning(f"Could not mark {message_id} as read: {e}")
