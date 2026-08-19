"""Outbound notification channels: Telegram, Discord webhook, email (SMTP).

Each channel is inert until the user supplies credentials in Settings.
Browser, sound, and desktop notifications are handled client-side.
"""

from __future__ import annotations

import logging
import smtplib
from email.mime.text import MIMEText

import requests

log = logging.getLogger("sentinel.notify")


def send_telegram(cfg: dict, text: str) -> bool:
    token, chat_id = cfg.get("bot_token"), cfg.get("chat_id")
    if not token or not chat_id:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text}, timeout=10)
        return r.ok
    except Exception as e:
        log.warning("telegram send failed: %s", e)
        return False


def send_discord(cfg: dict, text: str) -> bool:
    url = cfg.get("webhook_url")
    if not url:
        return False
    try:
        r = requests.post(url, json={"content": text[:1900]}, timeout=10)
        return r.ok
    except Exception as e:
        log.warning("discord send failed: %s", e)
        return False


def send_email(cfg: dict, subject: str, text: str) -> bool:
    host = cfg.get("smtp_host")
    to = cfg.get("to")
    if not host or not to:
        return False
    try:
        msg = MIMEText(text)
        msg["Subject"] = subject
        msg["From"] = cfg.get("from", cfg.get("user", "sentinel@localhost"))
        msg["To"] = to
        with smtplib.SMTP(host, int(cfg.get("port", 587)), timeout=15) as s:
            if cfg.get("starttls", True):
                s.starttls()
            if cfg.get("user"):
                s.login(cfg["user"], cfg.get("password", ""))
            s.send_message(msg)
        return True
    except Exception as e:
        log.warning("email send failed: %s", e)
        return False


def dispatch(channels: list[str], channel_cfg: dict, subject: str, text: str):
    """Fire all configured server-side channels. Client channels are skipped."""
    if "telegram" in channels:
        send_telegram(channel_cfg.get("telegram", {}), text)
    if "discord" in channels:
        send_discord(channel_cfg.get("discord", {}), text)
    if "email" in channels:
        send_email(channel_cfg.get("email", {}), subject, text)
