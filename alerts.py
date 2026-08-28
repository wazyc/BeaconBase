"""障害・回復の通知。

alerts セクションが無くても alerts.log には残す。
Webhook / コマンド / メールは設定があるときだけ送る。通知失敗で監視自体は止めない。
"""

from __future__ import annotations

import logging
import os
import smtplib
import subprocess
from datetime import datetime
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests

from status_store import AlertEvent

logger = logging.getLogger("BeaconBase")


def _expand(value: Optional[str]) -> str:
    if not value:
        return ""
    return os.path.expandvars(os.path.expanduser(str(value))).strip()


def format_duration(seconds: Optional[float]) -> str:
    """秒を人が読める長さにする。"""
    if seconds is None:
        return ""
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}時間{minutes}分"
    if minutes:
        return f"{minutes}分{secs}秒"
    return f"{secs}秒"


def format_event_line(event: AlertEvent) -> str:
    label = {"down": "障害", "recover": "回復", "remind": "継続"}.get(event.kind, event.kind)
    duration = format_duration(event.duration_seconds)
    extra = f" 継続 {duration}" if duration and event.kind != "recover" else ""
    if event.kind == "recover" and duration:
        extra = f" 障害時間 {duration}"
    return f"[{label}] {event.category}/{event.name}: {event.status} — {event.message}{extra}"


def format_events_text(events: List[AlertEvent]) -> str:
    lines = ["BeaconBase 監視通知", ""]
    lines.extend(format_event_line(e) for e in events)
    return "\n".join(lines)


class AlertDispatcher:
    """alerts.log と外部通知を扱う。"""

    def __init__(self, config: Dict[str, Any], output_folder: str):
        alerts = config.get("alerts") if isinstance(config.get("alerts"), dict) else {}
        self.enabled = bool(alerts.get("enabled", True))
        self.output_folder = output_folder
        self.log_path = os.path.join(output_folder, "alerts.log")
        self.webhook = alerts.get("webhook") if isinstance(alerts.get("webhook"), dict) else {}
        self.command = _expand(alerts.get("command"))
        email = alerts.get("email") if isinstance(alerts.get("email"), dict) else {}
        self.email = email

    def notify(self, events: List[AlertEvent]) -> None:
        if not events:
            return
        self._append_log(events)
        if not self.enabled:
            return
        text = format_events_text(events)
        try:
            self._send_webhook(text, events)
        except Exception as e:
            logger.warning(f"Webhook 通知に失敗しました: {e}")
        try:
            self._run_command(text)
        except Exception as e:
            logger.warning(f"通知コマンドの実行に失敗しました: {e}")
        try:
            self._send_email(text)
        except Exception as e:
            logger.warning(f"メール通知に失敗しました: {e}")

    def _append_log(self, events: List[AlertEvent]) -> None:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        os.makedirs(self.output_folder, exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as f:
            for event in events:
                f.write(f"{stamp} {format_event_line(event)}\n")

    def _send_webhook(self, text: str, events: List[AlertEvent]) -> None:
        url = _expand(self.webhook.get("url"))
        if not url:
            return
        fmt = str(self.webhook.get("format") or "auto").lower()
        if fmt == "auto":
            host = (urlparse(url).hostname or "").lower()
            if "discord.com" in host or "discordapp.com" in host:
                fmt = "discord"
            elif "hooks.slack.com" in host:
                fmt = "slack"
            else:
                fmt = "generic"
        timeout = float(self.webhook.get("timeout", 10))
        if fmt == "discord":
            payload: Dict[str, Any] = {"content": text[:1900]}
        elif fmt == "slack":
            payload = {"text": text}
        else:
            payload = {
                "text": text,
                "events": [event.to_dict() for event in events],
            }
        response = requests.post(url, json=payload, timeout=timeout)
        response.raise_for_status()

    def _run_command(self, text: str) -> None:
        if not self.command:
            return
        subprocess.run(
            self.command,
            input=text,
            text=True,
            shell=True,
            check=True,
            timeout=30,
        )

    def _send_email(self, text: str) -> None:
        smtp_host = _expand(self.email.get("smtp_host"))
        if not smtp_host:
            return
        recipients = self.email.get("to") or []
        if isinstance(recipients, str):
            recipients = [recipients]
        recipients = [str(r).strip() for r in recipients if str(r).strip()]
        if not recipients:
            logger.warning("alerts.email.to が空のためメールを送りません")
            return
        port = int(self.email.get("smtp_port", 587))
        username = _expand(self.email.get("username"))
        password = _expand(self.email.get("password")) or os.environ.get(
            "BEACONBASE_SMTP_PASSWORD", ""
        )
        from_addr = _expand(self.email.get("from")) or username or recipients[0]
        use_tls = bool(self.email.get("use_tls", True))
        subject = self.email.get("subject") or "BeaconBase 監視通知"

        msg = MIMEText(text, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = from_addr
        msg["To"] = ", ".join(recipients)

        with smtplib.SMTP(smtp_host, port, timeout=20) as smtp:
            if use_tls:
                smtp.starttls()
            if username:
                smtp.login(username, password)
            smtp.sendmail(from_addr, recipients, msg.as_string())
