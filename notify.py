"""Alert channels: ntfy push (phone) and Gmail. Both configured by env vars."""
import json
import os
import smtplib
import urllib.request
from email.message import EmailMessage
from typing import List

PRIORITY = {"urgent": 5, "high": 4, "default": 3, "low": 2}


def ntfy(title: str, message: str, url: str = "", priority: str = "default", tags=()) -> None:
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    body = {"topic": topic, "title": title, "message": message,
            "priority": PRIORITY.get(priority, 3), "tags": list(tags)}
    if url:
        body["click"] = url
        body["actions"] = [{"action": "view", "label": "Open store page", "url": url}]
    req = urllib.request.Request(server, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=15).read()


def email(subject: str, lines: List[str]) -> None:
    user, pw = os.environ.get("GMAIL_USER"), os.environ.get("GMAIL_APP_PASSWORD")
    if not (user and pw):
        return
    user, pw = user.strip(), pw.replace(" ", "").strip()  # Google shows app passwords as "abcd efgh ..."
    msg = EmailMessage()
    msg["From"] = user
    msg["To"] = (os.environ.get("ALERT_EMAIL_TO") or user).strip()
    msg["Subject"] = subject
    msg.set_content("\n\n".join(lines))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as s:
        s.login(user, pw)
        s.send_message(msg)


def channels_configured() -> List[str]:
    out = []
    if os.environ.get("NTFY_TOPIC"):
        out.append("ntfy")
    if os.environ.get("GMAIL_USER") and os.environ.get("GMAIL_APP_PASSWORD"):
        out.append("email")
    return out
