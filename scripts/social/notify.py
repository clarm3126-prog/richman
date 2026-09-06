#!/usr/bin/env python3
"""운영자 텔레그램 알림. 기존 봇 토큰을 그대로 쓴다."""
import os

import requests


def tell_owner(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not (token and chat_id):
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=20,
        )
        return r.json().get("ok", False)
    except Exception as e:
        print(f"  ! 텔레그램 알림 실패: {e}")
        return False
