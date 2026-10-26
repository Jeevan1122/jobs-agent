import logging
import asyncio
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, MessageHandler, filters, ContextTypes

import config
import db
from gmail.reply_sender import send_reply
from submitter.playwright_submit import submit_application

log = logging.getLogger(__name__)

_bot = None
_pending_edits = {}  # chat_id -> email db_id awaiting edited reply text


def _get_bot():
    global _bot
    if _bot is None:
        _bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
    return _bot


def _visa_emoji(visa_flag):
    return "✅" if visa_flag == "ok" else "⚠️"


async def send_job_approval_request(job_dict, ats_result, resume_tex_path):
    bot = _get_bot()
    job_id = job_dict["job_id"]
    matched = ", ".join(ats_result.get("matched_keywords", [])[:5]) or "none"
    missing = ", ".join(ats_result.get("missing_required", [])[:3]) or "none"
    visa_flag = ats_result.get("visa_flag", job_dict.get("visa_flag", "ok"))

    text = (
        f"🔍 *NEW JOB FOUND*\n"
        f"*Company:* {job_dict.get('company', 'Unknown')}\n"
        f"*Role:* {job_dict.get('title', 'Unknown')}\n"
        f"*Location:* {job_dict.get('location', 'Unknown')}\n"
        f"*ATS Score:* {ats_result.get('ats_score', 0)}%\n"
        f"*Visa:* {_visa_emoji(visa_flag)} {visa_flag.replace('_', ' ').title()}\n\n"
        f"*Matched:* {matched}\n"
        f"*Missing:* {missing}\n\n"
        f"*Notes:* {ats_result.get('notes', '')}"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Apply", callback_data=f"job_apply|{job_id}"),
            InlineKeyboardButton("❌ Skip", callback_data=f"job_skip|{job_id}"),
        ]
    ])

    await bot.send_message(
        chat_id=config.TELEGRAM_CHAT_ID,
        text=text,
        parse_mode="Markdown",
        reply_markup=keyboard,
    )
    log.info(f"Sent job approval request for {job_dict.get('company')} — {job_dict.get('title')}")


async def send_email_approval_request(email_dict, classification, db_email_id):
    bot = _get_bot()
    category = classification.get("category", "unknown")
    company = classification.get("company", "unknown")
    role = classification.get("role", "unknown")
    draft = classification.get("draft_reply", "")
    preview = (email_dict.get("body_preview") or "")[:300]

    urgency_prefix = "🚨 URGENT — " if classification.get("urgency") == "high" else ""

    text = (
        f"📧 *{urgency_prefix}RECRUITER EMAIL*\n"
        f"*From:* {email_dict.get('sender', '')}\n"
        f"*Subject:* {email_dict.get('subject', '')}\n"
        f"*Category:* {category.replace('_', ' ').title()}\n"
        f"*Company:* {company} | *Role:* {role}\n\n"
        f"*Preview:*\n{preview}\n\n"
        f"*Draft Reply:*\n_{draft[:400]}_"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Send Reply", callback_data=f"email_send|{db_email_id}"),
            InlineKeyboardButton("✏️ Edit Reply", callback_data=f"email_edit|{db_email_id}"),
            InlineKeyboardButton("❌ Skip", callback_data=f"email_skip|{db_email_id}"),
        ]
    ])

    await bot.send_message(
        chat_id=config.TELEGRAM_CHAT_ID,
        text=text,
        parse_mode="Markdown",
        reply_markup=keyboard,
    )
    log.info(f"Sent email approval request for email db_id={db_email_id}")


async def _handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if "|" not in data:
        return

    action, entity_id = data.split("|", 1)

    if action == "job_apply":
        job = db.get_job_by_id(entity_id)
        if job:
            result = submit_application(job, job.get("resume_pdf_path"), dry_run=False)
            if result.get("success"):
                db.update_job_status(entity_id, "applied")
                await query.edit_message_text(f"✅ Applied to {job.get('company')} — {job.get('title')}")
            else:
                db.update_job_status(entity_id, "failed")
                await query.edit_message_text(f"❌ Submission failed: {result.get('error')}")
        else:
            await query.edit_message_text("⚠️ Job not found in database.")

    elif action == "job_skip":
        db.update_job_status(entity_id, "skipped")
        await query.edit_message_text(f"⏭️ Skipped job {entity_id}")

    elif action == "email_send":
        db_id = int(entity_id)
        email = db.get_email_by_db_id(db_id)
        if email:
            sender = email.get("sender", "")
            to_email = sender.split("<")[-1].rstrip(">") if "<" in sender else sender
            result = send_reply(
                gmail_message_id=email["gmail_message_id"],
                gmail_thread_id=email["gmail_thread_id"],
                to_email=to_email,
                subject=email.get("subject", ""),
                body=email.get("draft_reply", ""),
            )
            if result.get("success"):
                db.update_email_status(db_id, "sent", reply_sent=email.get("draft_reply"))
                await query.edit_message_text(f"✅ Reply sent to {to_email}")
            else:
                await query.edit_message_text(f"❌ Send failed: {result.get('error')}")
        else:
            await query.edit_message_text("⚠️ Email not found in database.")

    elif action == "email_edit":
        db_id = int(entity_id)
        _pending_edits[query.message.chat_id] = db_id
        await query.edit_message_text(
            f"✏️ Send your edited reply text now. It will replace the draft for email #{db_id}."
        )

    elif action == "email_skip":
        db_id = int(entity_id)
        db.update_email_status(db_id, "skipped")
        await query.edit_message_text(f"⏭️ Skipped email #{db_id}")


async def _handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in _pending_edits:
        db_id = _pending_edits.pop(chat_id)
        new_reply = update.message.text.strip()
        email = db.get_email_by_db_id(db_id)
        if email:
            sender = email.get("sender", "")
            to_email = sender.split("<")[-1].rstrip(">") if "<" in sender else sender
            result = send_reply(
                gmail_message_id=email["gmail_message_id"],
                gmail_thread_id=email["gmail_thread_id"],
                to_email=to_email,
                subject=email.get("subject", ""),
                body=new_reply,
            )
            if result.get("success"):
                db.update_email_status(db_id, "sent", reply_sent=new_reply)
                await update.message.reply_text(f"✅ Edited reply sent to {to_email}")
            else:
                await update.message.reply_text(f"❌ Send failed: {result.get('error')}")
        else:
            await update.message.reply_text("⚠️ Email not found.")


def run_bot():
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CallbackQueryHandler(_handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_message))
    log.info("Telegram bot polling started.")
    app.run_polling()
