"""Alerts: HTML email digest (smtplib) and Telegram message (Bot API)."""

import html
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

import requests
from loguru import logger

from src.cleaner import JobRecord
from src.config_loader import AlertsConfig, get_env

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
TELEGRAM_MAX_CHARS = 4000
SMTP_TIMEOUT_SECONDS = 30


def format_salary(record: JobRecord) -> str:
    """Human-readable salary such as ``USD 120k-150k`` (empty string if unknown)."""
    if record.salary_min is None or record.salary_max is None:
        return ""
    low, high = record.salary_min / 1000, record.salary_max / 1000
    span = f"{low:.0f}k" if low == high else f"{low:.0f}k-{high:.0f}k"
    return f"{record.salary_currency or ''} {span}".strip()


def matches_keywords(record: JobRecord, keywords: list[str]) -> bool:
    """True if any keyword occurs in the job's title, company, location, skills or description."""
    if not keywords:
        return True
    haystack = " ".join(
        [record.title, record.company or "", record.location or "", " ".join(record.skills),
         record.description or "", "remote" if record.remote else ""]
    ).lower()
    return any(keyword.lower() in haystack for keyword in keywords)


def select_alerts(records: list[JobRecord], keywords: list[str]) -> list[JobRecord]:
    """Filter new records down to those matching the configured alert keywords."""
    return [record for record in records if matches_keywords(record, keywords)]


def build_html_digest(records: list[JobRecord], total_new: int, limit: int = 25) -> str:
    """Render an email-client-friendly HTML digest of the matching jobs."""
    rows = []
    for record in records[:limit]:
        title = html.escape(record.title)
        link = f'<a href="{html.escape(record.url)}" style="color:#7C5CFF;text-decoration:none">{title}</a>' if record.url else title
        chips = "".join(
            f'<span style="display:inline-block;background:#EEF0FF;color:#4A3FD8;border-radius:10px;'
            f'padding:2px 8px;margin:2px;font-size:11px">{html.escape(skill)}</span>'
            for skill in record.skills[:8]
        )
        meta = " · ".join(
            html.escape(part)
            for part in [record.company or "", record.location or "", "Remote" if record.remote else "", format_salary(record)]
            if part
        )
        rows.append(
            f'<tr><td style="padding:12px 0;border-bottom:1px solid #E6E8F0">'
            f'<div style="font-size:15px;font-weight:600">{link}</div>'
            f'<div style="font-size:12px;color:#5B6178;margin:2px 0 6px">{meta}</div>{chips}</td></tr>'
        )
    more = f"<p style='color:#5B6178;font-size:12px'>…and {len(records) - limit} more.</p>" if len(records) > limit else ""
    return (
        '<html><body style="font-family:Segoe UI,Arial,sans-serif;background:#F5F6FA;padding:24px">'
        '<table width="640" align="center" style="background:#fff;border-radius:12px;padding:24px">'
        '<tr><td><h2 style="margin:0;color:#1B1F3B">📡 SkillPulse digest</h2>'
        f'<p style="color:#5B6178">{total_new} new job(s) this run, {len(records)} match your keywords.</p></td></tr>'
        f'{"".join(rows)}<tr><td>{more}</td></tr></table></body></html>'
    )


class Notifier:
    """Sends alerts through every configured channel that has credentials in the environment."""

    def __init__(self, alerts: AlertsConfig) -> None:
        self.alerts = alerts

    # ----------------------------------------------------------------- email
    def send_email(self, subject: str, html_body: str) -> bool:
        """Send an HTML email via SMTP (STARTTLS on 587, SSL on 465). Returns True on success."""
        host, port = get_env("SMTP_HOST"), int(get_env("SMTP_PORT", "587") or 587)
        user, password = get_env("SMTP_USER"), get_env("SMTP_PASSWORD")
        recipient = get_env("ALERT_EMAIL_TO")
        if not (host and user and password and recipient):
            logger.info("Email alerts skipped: SMTP_* / ALERT_EMAIL_TO not fully configured")
            return False

        message = MIMEMultipart("alternative")
        message["Subject"], message["From"], message["To"] = subject, user, recipient
        message.attach(MIMEText("Open this email in an HTML-capable client to see the digest.", "plain"))
        message.attach(MIMEText(html_body, "html"))
        try:
            context = ssl.create_default_context()
            if port == 465:
                with smtplib.SMTP_SSL(host, port, context=context, timeout=SMTP_TIMEOUT_SECONDS) as server:
                    server.login(user, password)
                    server.sendmail(user, [recipient], message.as_string())
            else:
                with smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT_SECONDS) as server:
                    server.starttls(context=context)
                    server.login(user, password)
                    server.sendmail(user, [recipient], message.as_string())
            logger.success("Email digest sent to {}", recipient)
            return True
        except (smtplib.SMTPException, OSError) as exc:
            logger.error("Email alert failed: {}", exc)
            return False

    # -------------------------------------------------------------- telegram
    def send_telegram(self, text: str) -> bool:
        """Send a message through the Telegram Bot API. Returns True on success."""
        token, chat_id = get_env("TELEGRAM_BOT_TOKEN"), get_env("TELEGRAM_CHAT_ID")
        if not (token and chat_id):
            logger.info("Telegram alerts skipped: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set")
            return False
        try:
            response = requests.post(
                TELEGRAM_API.format(token=token),
                json={"chat_id": chat_id, "text": text[:TELEGRAM_MAX_CHARS], "parse_mode": "HTML",
                      "disable_web_page_preview": True},
                timeout=SMTP_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            logger.success("Telegram alert sent")
            return True
        except requests.RequestException as exc:
            logger.error("Telegram alert failed: {}", exc)
            return False

    @staticmethod
    def build_telegram_text(records: list[JobRecord], total_new: int, limit: int = 10) -> str:
        """Compact HTML message listing the top matching jobs."""
        lines = [f"📡 <b>SkillPulse</b>: {total_new} new job(s), {len(records)} match your keywords"]
        for record in records[:limit]:
            parts = [part for part in [record.company, "Remote" if record.remote else record.location, format_salary(record)] if part]
            title = html.escape(record.title)
            link = f'<a href="{html.escape(record.url)}">{title}</a>' if record.url else title
            lines.append(f"• {link} - {html.escape(' · '.join(parts))}")
        return "\n".join(lines)

    # --------------------------------------------------------------- orchestration
    def notify(self, new_records: list[JobRecord]) -> dict[str, Optional[bool]]:
        """Send alerts for new records matching the keywords; returns per-channel results."""
        results: dict[str, Optional[bool]] = {}
        if not self.alerts.enabled or len(new_records) < self.alerts.min_new_records:
            logger.info("No alerts to send ({} new records)", len(new_records))
            return results
        matching = select_alerts(new_records, self.alerts.keywords)
        if not matching:
            logger.info("{} new records, none matched alert keywords", len(new_records))
            return results

        if "email" in self.alerts.channels:
            subject = f"SkillPulse: {len(matching)} new matching job(s)"
            digest = build_html_digest(matching, len(new_records), self.alerts.max_items_in_digest)
            results["email"] = self.send_email(subject, digest)
        if "telegram" in self.alerts.channels:
            results["telegram"] = self.send_telegram(self.build_telegram_text(matching, len(new_records)))
        return results
