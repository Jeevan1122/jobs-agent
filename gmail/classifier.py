import json
import logging

from groq import Groq

import config

log = logging.getLogger(__name__)
_client = None

CANDIDATE = config.CANDIDATE


def _get_client():
    global _client
    if _client is None:
        _client = Groq(api_key=config.GROQ_API_KEY)
    return _client


REPLY_GUIDANCE = {
    "recruiter_outreach": (
        "Write a warm, professional reply expressing genuine interest. "
        "State that you are on F-1 STEM OPT authorization valid through July 2028 "
        "and confirm you are legally authorized to work in the US on a W-2 basis. "
        "Ask politely whether the role supports W-2 employment (not 1099/C2C). "
        "If no job description was attached, ask them to share it. "
        "Keep it under 150 words."
    ),
    "interview_invite": (
        "Write a professional, enthusiastic reply confirming interest in interviewing. "
        "Express availability and ask for the interview format (phone/video/onsite), "
        "duration, and who you will be speaking with. Keep it under 120 words."
    ),
    "rejection": (
        "Write a brief, gracious acknowledgment thanking them for their time "
        "and expressing interest in future opportunities. Under 60 words."
    ),
    "offer": (
        "DO NOT draft a reply. This is an offer — it requires careful manual review. "
        "Set draft_reply to: 'URGENT: Offer received — manual review required before responding.'"
    ),
    "unknown": (
        "Write a neutral professional reply acknowledging the email and asking "
        "for clarification on how to proceed. Under 80 words."
    ),
}


def classify_email(email_dict):
    subject = email_dict.get("subject", "")
    sender = email_dict.get("sender", "")
    body_preview = email_dict.get("body_preview", "")

    system_prompt = (
        "You are an assistant that classifies recruiter emails for a job seeker "
        "and drafts professional replies. Return only valid JSON."
    )

    reply_instructions = json.dumps(REPLY_GUIDANCE)

    user_prompt = f"""Classify this email and draft a reply.

FROM: {sender}
SUBJECT: {subject}
BODY PREVIEW: {body_preview}

CANDIDATE: {CANDIDATE['name']}
VISA STATUS: F-1 STEM OPT, authorized through July 2028, W-2 only (not 1099/C2C)

REPLY GUIDANCE PER CATEGORY:
{reply_instructions}

Return ONLY valid JSON:
{{
  "category": "recruiter_outreach" | "interview_invite" | "rejection" | "offer" | "unknown",
  "company": "company name or unknown",
  "role": "role title or unknown",
  "urgency": "high" | "normal" | "low",
  "draft_reply": "full reply text here",
  "should_notify": true | false
}}

Rules:
- offer → urgency = "high", should_notify = true
- interview_invite → urgency = "high", should_notify = true
- recruiter_outreach → urgency = "normal", should_notify = true
- rejection → urgency = "low", should_notify = false
- unknown → urgency = "low", should_notify = true
"""

    try:
        response = _get_client().chat.completions.create(
            model="qwen/qwen3.8-27b",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        result = json.loads(response.choices[0].message.content)
        return result
    except Exception as e:
        log.error(f"Email classification failed: {e}")
        return {
            "category": "unknown",
            "company": "unknown",
            "role": "unknown",
            "urgency": "low",
            "draft_reply": "Thank you for reaching out. Could you please provide more details?",
            "should_notify": True,
        }
