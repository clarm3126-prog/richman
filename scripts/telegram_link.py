#!/usr/bin/env python3
"""
텔레그램 계정 연결.

사용자가 봇에게 6자리 코드(profiles.link_code)를 보내면,
그 대화방 번호(chat_id)를 해당 사용자 프로필에 저장한다.
5분마다 실행되며, 이미 처리한 메시지는 offset 파일로 건너뛴다.
"""
import json
import os
import re
import sys
from pathlib import Path

import requests

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()

OFFSET_FILE = Path("data/telegram_offset.json")
CODE_RE = re.compile(r"\b([A-Fa-f0-9]{6})\b")

HEADERS = {
    "apikey": SERVICE_KEY,
    "Authorization": f"Bearer {SERVICE_KEY}",
    "Content-Type": "application/json",
}


def tg(method, **params):
    r = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/{method}", json=params, timeout=20
    )
    return r.json()


def send(chat_id, text):
    tg("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML")


def load_offset():
    try:
        return json.loads(OFFSET_FILE.read_text()).get("offset", 0)
    except Exception:
        return 0


def save_offset(offset):
    OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
    OFFSET_FILE.write_text(json.dumps({"offset": offset}))


def link_code_to_chat(code, chat_id):
    """코드에 해당하는 프로필에 chat_id 저장. 성공하면 True."""
    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/profiles",
        headers={**HEADERS, "Prefer": "return=representation"},
        params={"link_code": f"eq.{code.upper()}"},
        json={"telegram_chat_id": str(chat_id)},
        timeout=20,
    )
    if r.status_code >= 300:
        print(f"  ! Supabase 오류 {r.status_code}: {r.text[:200]}")
        return False
    return bool(r.json())


def main():
    if not (BOT_TOKEN and SUPABASE_URL and SERVICE_KEY):
        print("환경변수 누락 (TELEGRAM_BOT_TOKEN / SUPABASE_URL / SUPABASE_SERVICE_KEY)")
        sys.exit(0)

    offset = load_offset()
    res = tg("getUpdates", offset=offset + 1, timeout=0, limit=100)
    if not res.get("ok"):
        print("getUpdates 실패:", res)
        sys.exit(0)

    updates = res.get("result", [])
    print(f"새 메시지 {len(updates)}건")

    max_id = offset
    for u in updates:
        max_id = max(max_id, u.get("update_id", 0))
        msg = u.get("message") or u.get("edited_message")
        if not msg:
            continue
        chat_id = msg.get("chat", {}).get("id")
        text = (msg.get("text") or "").strip()
        if not chat_id or not text:
            continue

        if text.startswith("/start"):
            send(
                chat_id,
                "📓 <b>종목노트 알림 봇</b>\n\n"
                "앱에서 로그인 후 <b>연결 코드</b>를 확인하고,\n"
                "그 6자리 코드를 여기로 보내주세요.\n\n"
                "연결되면 관심종목 목표가 도달 시 알림을 보내드립니다.",
            )
            continue

        m = CODE_RE.search(text)
        if not m:
            send(chat_id, "❓ 6자리 연결 코드를 보내주세요. (앱 → ⭐ 관심 탭에서 확인)")
            continue

        code = m.group(1).upper()
        if link_code_to_chat(code, chat_id):
            print(f"  연결 성공: {code} → {chat_id}")
            send(chat_id, f"✅ 연결 완료! (코드 {code})\n이제 이 대화방으로 알림이 전송됩니다.")
        else:
            print(f"  코드 없음: {code}")
            send(chat_id, "❌ 일치하는 코드가 없습니다. 앱에서 코드를 다시 확인해주세요.")

    if max_id != offset:
        save_offset(max_id)
        print(f"offset 갱신: {offset} → {max_id}")


if __name__ == "__main__":
    main()
