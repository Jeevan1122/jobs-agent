import os
import re
import logging
from datetime import datetime

import config

log = logging.getLogger(__name__)

NEVER_FABRICATE = [
    "YARA", "dbt", "Rust", "Fivetran", "Alteryx", "SAS", "MISP", "STIX",
    "Snowflake", "C++", "C#", "Kubernetes", "MariaDB", "SSIS", "Spring Boot",
]

RESUME_HTML_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: 'Arial', sans-serif; font-size: 10pt; color: #1a1a1a; padding: 0.5in; line-height: 1.4; }}
  h1 {{ font-size: 18pt; font-weight: bold; margin-bottom: 2px; }}
  .contact {{ font-size: 9pt; color: #444; margin-bottom: 10px; }}
  .contact a {{ color: #1a1a1a; text-decoration: none; }}
  h2 {{ font-size: 11pt; font-weight: bold; border-bottom: 1.5px solid #1a1a1a; margin: 12px 0 5px; text-transform: uppercase; letter-spacing: 0.5px; }}
  .entry {{ margin-bottom: 8px; }}
  .entry-header {{ display: flex; justify-content: space-between; align-items: baseline; }}
  .entry-title {{ font-weight: bold; font-size: 10pt; }}
  .entry-date {{ font-size: 9pt; color: #555; white-space: nowrap; }}
  .entry-sub {{ font-style: italic; font-size: 9pt; color: #333; margin-bottom: 3px; }}
  ul {{ margin-left: 16px; }}
  li {{ margin-bottom: 2px; font-size: 9.5pt; }}
  .skills-grid {{ display: flex; flex-wrap: wrap; gap: 4px; }}
  .skill-tag {{ background: #f0f0f0; padding: 1px 7px; border-radius: 3px; font-size: 9pt; }}
</style>
</head>
<body>
{content}
</body>
</html>"""

TAILOR_SYSTEM_PROMPT = """You are an expert ATS resume writer. Given a candidate's existing resume text and a job description, produce a tailored single-page HTML resume body.

ABSOLUTE RULES:
1. NEVER invent experience, skills, or dates the candidate does not have.
2. NEVER add these skills if the candidate doesn't have them: {never_fabricate}
3. Only rewrite, reorder, and emphasize what is already present.
4. Surface matched JD keywords naturally in bullet points.
5. Return ONLY the HTML body content (no <html>/<head>/<body> tags).
6. Keep it to one printed page worth of content.

STRUCTURE:
- Name + contact line at top
- Professional Summary (2-3 sentences using JD keywords the candidate actually has)
- Skills section (keyword-rich, matched keywords first)
- Experience (most relevant first, rewrite bullets to mirror JD phrasing)
- Education
- Projects (most relevant first)
""".format(never_fabricate=", ".join(NEVER_FABRICATE))


def _get_client():
    from groq import Groq
    return Groq(api_key=config.GROQ_API_KEY)


def _call_llm(system_prompt, user_prompt):
    client = _get_client()
    response = client.chat.completions.create(
        model="qwen/qwen3.8-27b",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        temperature=0.2,
        max_tokens=3000,
    )
    return response.choices[0].message.content.strip()


def _sanitize(text):
    return re.sub(r"[^a-zA-Z0-9_-]", "_", text)[:40]


def extract_resume_text(pdf_path):
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            pages = [p.extract_text() or "" for p in pdf.pages]
        return "\n".join(pages).strip()
    except Exception as e:
        log.error(f"PDF text extraction failed: {e}")
        return ""


def _html_to_pdf(html_content, output_path):
    from playwright.sync_api import sync_playwright
    full_html = RESUME_HTML_TEMPLATE.format(content=html_content)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(full_html, wait_until="networkidle")
        page.pdf(
            path=output_path,
            format="Letter",
            margin={"top": "0.5in", "bottom": "0.5in", "left": "0.5in", "right": "0.5in"},
            print_background=True,
        )
        browser.close()


def tailor_resume(jd_text, job_title, company, ats_result, pdf_path=None):
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)

    if os.path.exists(config.MASTER_RESUME_PATH):
        return _tailor_latex(jd_text, job_title, company, ats_result)

    source_pdf = pdf_path or os.path.join(config.RESUME_DIR, "uploaded_resume.pdf")
    if not os.path.exists(source_pdf):
        return {"tex_path": None, "pdf_path": None, "success": False,
                "error": "No resume found — upload your PDF on the profile page first."}

    return _tailor_from_pdf(source_pdf, jd_text, job_title, company, ats_result)


def _tailor_from_pdf(source_pdf, jd_text, job_title, company, ats_result):
    resume_text = extract_resume_text(source_pdf)
    if not resume_text:
        return {"tex_path": None, "pdf_path": None, "success": False,
                "error": "Could not extract text from PDF — make sure the PDF is not scanned/image-only."}

    matched = ", ".join(ats_result.get("matched_keywords", [])[:15])
    missing = ", ".join(ats_result.get("missing_required", [])[:10])

    user_prompt = f"""Tailor this resume for the job below.

JOB TITLE: {job_title}
COMPANY: {company}

JOB DESCRIPTION:
{jd_text[:4000]}

ATS ANALYSIS:
- Matched keywords (emphasize these): {matched}
- Missing required (DO NOT fabricate): {missing}

CANDIDATE'S CURRENT RESUME:
{resume_text[:4000]}

Return the complete tailored resume as HTML body content only (no <html>/<head>/<body> tags)."""

    try:
        tailored_html = _call_llm(TAILOR_SYSTEM_PROMPT, user_prompt)
        tailored_html = re.sub(r"(?i)^```html\s*|```\s*$", "", tailored_html).strip()
    except Exception as e:
        log.error(f"Resume tailoring LLM call failed: {e}")
        return {"tex_path": None, "pdf_path": None, "success": False, "error": str(e)}

    date_str = datetime.now().strftime("%Y%m%d_%H%M")
    filename = f"{_sanitize(company)}_{_sanitize(job_title)}_{date_str}"
    pdf_out = os.path.join(config.OUTPUT_DIR, f"{filename}.pdf")

    try:
        _html_to_pdf(tailored_html, pdf_out)
        if os.path.exists(pdf_out):
            log.info(f"Tailored PDF saved: {pdf_out}")
            return {"tex_path": None, "pdf_path": pdf_out, "success": True}
        return {"tex_path": None, "pdf_path": None, "success": False, "error": "PDF not created"}
    except Exception as e:
        log.error(f"HTML→PDF failed: {e}")
        return {"tex_path": None, "pdf_path": None, "success": False, "error": str(e)}


def _tailor_latex(jd_text, job_title, company, ats_result):
    import subprocess
    with open(config.MASTER_RESUME_PATH) as f:
        master_tex = f.read()

    matched = ", ".join(ats_result.get("matched_keywords", [])[:15])
    missing = ", ".join(ats_result.get("missing_required", [])[:10])

    user_prompt = f"""Tailor this LaTeX resume for the job below.

JOB TITLE: {job_title} at {company}
JOB DESCRIPTION: {jd_text[:5000]}
Matched keywords (emphasize): {matched}
Missing required (DO NOT fabricate): {missing}

MASTER RESUME (LaTeX):
{master_tex}

Return ONLY the complete tailored LaTeX source."""

    try:
        tailored_tex = _call_llm(
            "You are an expert ATS resume tailor. Return ONLY complete LaTeX source, no markdown.",
            user_prompt,
        )
    except Exception as e:
        return {"tex_path": None, "pdf_path": None, "success": False, "error": str(e)}

    date_str = datetime.now().strftime("%Y%m%d_%H%M")
    filename = f"{_sanitize(company)}_{_sanitize(job_title)}_{date_str}"
    tex_path = os.path.join(config.OUTPUT_DIR, f"{filename}.tex")
    pdf_path = os.path.join(config.OUTPUT_DIR, f"{filename}.pdf")

    with open(tex_path, "w") as f:
        f.write(tailored_tex)

    try:
        result = subprocess.run(
            ["pdflatex", "-interaction=nonstopmode", "-output-directory", config.OUTPUT_DIR, tex_path],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0 and os.path.exists(pdf_path):
            return {"tex_path": tex_path, "pdf_path": pdf_path, "success": True}
        return {"tex_path": tex_path, "pdf_path": None, "success": False, "error": "pdflatex failed"}
    except FileNotFoundError:
        return {"tex_path": tex_path, "pdf_path": None, "success": False, "error": "pdflatex not installed"}
    except subprocess.TimeoutExpired:
        return {"tex_path": tex_path, "pdf_path": None, "success": False, "error": "pdflatex timed out"}
