import json
import logging
import re

from groq import Groq

import config

log = logging.getLogger(__name__)
_client = None

GROQ_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"
GROQ_MODEL_FALLBACK = "llama-3.1-8b-instant"


def _get_client():
    global _client
    if _client is None:
        _client = Groq(api_key=config.GROQ_API_KEY)
    return _client


def check_visa_disqualifiers(jd_text, visa_status=None):
    """
    Check job for visa disqualifiers.
    Only applies sponsorship/OPT filters for non-citizens (F-1, H-1B, TN, etc.).
    US Citizens and Green Card holders skip sponsorship checks.
    """
    text_lower = jd_text.lower()
    no_sponsorship_needed = visa_status and any(
        v in (visa_status or "").lower()
        for v in ["citizen", "green card", "gc", "permanent resident"]
    )

    for phrase in config.VISA_HARD_DISQUALIFIERS:
        # Skip sponsorship-related checks for citizens/GC holders
        if no_sponsorship_needed and any(
            word in phrase.lower() for word in ["sponsor", "opt", "f-1", "1099", "c2c", "corp"]
        ):
            continue
        if phrase.lower() in text_lower:
            return {"status": "hard_stop", "reason": f"Hard disqualifier: '{phrase}'"}

    gray_hits = [p for p in config.VISA_GRAY_AREA if p.lower() in text_lower]
    if gray_hits and not no_sponsorship_needed:
        return {"status": "gray_area", "reason": f"Gray area: {gray_hits}"}
    return {"status": "ok", "reason": "No visa issues."}


def _keyword_score(jd_text, job_title, candidate_skills):
    """
    Pure keyword-based ATS scoring — no API needed.
    Used as primary method or fallback when Groq is unavailable.
    """
    if candidate_skills and isinstance(candidate_skills, str) and candidate_skills.strip():
        skills = [s.strip().lower() for s in candidate_skills.split(",") if s.strip()]
    else:
        skills = [s.lower() for s in config.CANDIDATE_SKILLS]

    jd_lower = jd_text.lower()
    matched = []
    for skill in skills:
        # Match whole word / phrase
        pattern = r'\b' + re.escape(skill) + r'\b'
        if re.search(pattern, jd_lower):
            matched.append(skill)

    # Find "required" keywords in JD that candidate might be missing
    # Look for lines with "required" or "must have" near a skill word
    required_section = re.findall(
        r'(?:required|must have|must-have|requirements?)[^\n.]*?([A-Za-z+#./ ]{3,30})',
        jd_lower
    )
    missing = []
    for term in required_section:
        term = term.strip()
        if len(term) > 2 and term not in [s.lower() for s in matched]:
            # Check if it looks like a tech skill (not a stop word)
            stop = {"the", "and", "for", "with", "you", "our", "are", "have", "will",
                    "this", "that", "from", "they", "been", "more", "also", "than"}
            if term not in stop and not any(s in term for s in matched[:10]):
                missing.append(term)
    missing = list(dict.fromkeys(missing))[:6]  # dedupe, cap at 6

    # Score = matched skills / expected skills in JD
    # Cap expected at 8 — long JDs shouldn't demand 20 matches from a ~50-skill list
    jd_word_count = len(jd_lower.split())
    expected = max(5, min(8, jd_word_count // 200))
    score = min(100, round((len(matched) / expected) * 100))

    # Boost score if job title words appear in candidate skills
    title_words = [w.lower() for w in job_title.split() if len(w) > 3]
    for tw in title_words:
        if any(tw in s for s in skills):
            score = min(100, score + 10)
            break

    return {
        "score": score,
        "matched_keywords": [s.title() for s in matched[:12]],
        "missing_required": [s.title() for s in missing],
        "recommendation": "apply" if score >= config.JOB_SEARCH["min_ats_score"] else "skip",
        "notes": f"Keyword match: {len(matched)} skills found in JD.",
    }


def _groq_score(jd_text, job_title, candidate_skills, model):
    if candidate_skills and isinstance(candidate_skills, str) and candidate_skills.strip():
        skills_list = candidate_skills.strip()
    else:
        skills_list = ", ".join(config.CANDIDATE_SKILLS)

    prompt = f"""Score this job description against the candidate's skills.

JOB TITLE: {job_title}

JOB DESCRIPTION:
{jd_text[:3000]}

CANDIDATE'S SKILLS:
{skills_list}

Return ONLY valid JSON (no markdown, no extra text):
{{
  "score": <integer 0-100>,
  "matched_keywords": ["skill1", "skill2"],
  "missing_required": ["missing1", "missing2"],
  "recommendation": "apply" or "skip",
  "notes": "one sentence"
}}

score = percentage of required keywords the candidate has (0-100).
recommendation = "apply" if score >= {config.JOB_SEARCH['min_ats_score']}, else "skip".
"""
    resp = _get_client().chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        max_tokens=400,
    )
    raw = resp.choices[0].message.content.strip()
    # Strip markdown code fences if present
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    result = json.loads(raw)
    return result


def score_ats(jd_text, job_title, candidate_skills=None):
    # If no JD, fall back to keyword scoring immediately
    if not jd_text or len(jd_text.strip()) < 50:
        kw = _keyword_score("", job_title, candidate_skills)
        kw["score"] = 70  # give benefit of the doubt when no JD
        kw["recommendation"] = "apply"
        kw["notes"] = "No job description — defaulting to manual review."
        return kw

    # Try Groq primary model
    for model in [GROQ_MODEL, GROQ_MODEL_FALLBACK]:
        try:
            result = _groq_score(jd_text, job_title, candidate_skills, model)
            log.info(f"ATS scored via {model}: {result.get('score')}%")
            return result
        except Exception as e:
            log.warning(f"Groq model {model} failed: {e}")

    # Both Groq models failed — use keyword scoring as fallback
    log.info("Groq unavailable — using keyword-based ATS scoring")
    return _keyword_score(jd_text, job_title, candidate_skills)


def screen_job(job_dict, candidate_skills=None, visa_status=None):
    jd_text  = job_dict.get("description", "")
    job_title = job_dict.get("title", "")

    visa_result = check_visa_disqualifiers(jd_text, visa_status=visa_status)

    if visa_result["status"] == "hard_stop":
        return {
            "job_id":           job_dict["job_id"],
            "company":          job_dict.get("company"),
            "title":            job_title,
            "visa_flag":        "hard_stop",
            "visa_note":        visa_result["reason"],
            "ats_score":        0,
            "matched_keywords": [],
            "missing_required": [],
            "recommendation":   "skip",
            "notes":            visa_result["reason"],
        }

    ats = score_ats(jd_text, job_title, candidate_skills=candidate_skills)

    return {
        "job_id":           job_dict["job_id"],
        "company":          job_dict.get("company"),
        "title":            job_title,
        "location":         job_dict.get("location"),
        "url":              job_dict.get("url"),
        "visa_flag":        visa_result["status"],
        "visa_note":        visa_result["reason"],
        "ats_score":        ats.get("score", 0),
        "matched_keywords": ats.get("matched_keywords", []),
        "missing_required": ats.get("missing_required", []),
        "recommendation":   ats.get("recommendation", "skip"),
        "notes":            ats.get("notes", ""),
    }
