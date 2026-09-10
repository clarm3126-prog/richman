#!/usr/bin/env python3
"""운영자 텔레그램 알림. 기존 봇 토큰을 그대로 쓴다.

보내는 일 자체는 scripts/common.py가 한다. 이 파일은 운영자 대화방을
환경변수에서 찾는 것만 담당한다. 진입 스크립트가 전부 scripts/ 안에
있어서 `import common`이 그대로 된다.
"""
import os

from common import send_message


def tell_owner(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not (token and chat_id):
        return False
    ok, detail = send_message(token, chat_id, text)
    if not ok:
        print(f"  ! 텔레그램 알림 실패: {detail}")
    return ok
