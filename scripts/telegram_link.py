#!/usr/bin/env python3
"""
텔레그램 계정 연결.

사용자가 봇에게 6자리 코드(profiles.link_code)를 보내면,
그 대화방 번호(chat_id)를 해당 사용자 프로필에 저장한다.
5분마다 실행되며, 이미 처리한 메시지는 offset 파일로 건너뛴다.
"""
import os
import re
import secrets
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from common import load_json, mask, save_json, send_message, supabase_headers  # noqa: E402

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()

OFFSET_FILE = Path("data/telegram_offset.json")
CODE_RE = re.compile(r"\b([A-Fa-f0-9]{6})\b")

HEADERS = supabase_headers(SERVICE_KEY)


def tg(method, **params):
    r = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/{method}", json=params, timeout=20
    )
    return r.json()


def send(chat_id, text):
    ok, detail = send_message(BOT_TOKEN, chat_id, text, disable_preview=False)
    if not ok:
        print(f"  ! 발송 실패 {mask(chat_id)}: {detail}")
    return ok


def load_offset():
    try:
        return load_json(OFFSET_FILE, {}).get("offset", 0)
    except Exception:
        return 0


def save_offset(offset):
    save_json(OFFSET_FILE, {"offset": offset}, indent=None)


def new_code():
    return secrets.token_hex(3).upper()


def link_code_to_chat(code, chat_id):
    """코드에 해당하는 프로필에 chat_id를 저장한다. 성공하면 True.

    저장과 동시에 그 코드를 새 값으로 바꾼다. 코드를 그대로 두면
    화면 캡처나 로그로 한 번 새어 나간 코드가 계속 유효해서,
    남이 그 코드를 봇에 보내면 알림이 그 사람 대화방으로 넘어간다.
    """
    for attempt in range(5):
        r = requests.patch(
            f"{SUPABASE_URL}/rest/v1/profiles",
            headers={**HEADERS, "Prefer": "return=representation"},
            params={"link_code": f"eq.{code.upper()}"},
            json={"telegram_chat_id": str(chat_id), "link_code": new_code()},
            timeout=20,
        )
        if r.status_code < 300:
            return bool(r.json())
        # 새로 뽑은 코드가 이미 쓰이는 값이면 다른 값으로 다시 뽑는다.
        # 이때 응답 본문에는 남의 코드가 들어 있으므로 찍지 않는다.
        if r.status_code == 409:
            if attempt < 4:
                continue
            print("  ! 코드 재발급 실패 409 (중복)")
            return False
        print(f"  ! Supabase 오류 {r.status_code}: {r.text[:200]}")
        return False
    return False


def already_linked(chat_id):
    """이 대화방이 이미 어떤 프로필에 연결돼 있는지."""
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/profiles",
            headers=HEADERS,
            params={
                "telegram_chat_id": f"eq.{chat_id}",
                "select": "telegram_chat_id",
                "limit": 1,
            },
            timeout=20,
        )
        return r.status_code < 300 and bool(r.json())
    except Exception:
        return False


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
            print(f"  연결 성공: {mask(code)} -> {mask(chat_id)}")
            send(chat_id, "✅ 연결 완료!\n이제 이 대화방으로 알림이 전송됩니다.")
        elif already_linked(chat_id):
            # 코드는 연결과 동시에 새로 발급된다. 같은 코드를 두 번 보내면
            # 두 번째는 맞는 코드가 없으니, 이미 연결된 경우를 갈라낸다.
            print(f"  이미 연결됨: {mask(chat_id)}")
            send(chat_id, "✅ 이미 연결되어 있습니다.")
        else:
            print(f"  코드 없음: {mask(code)}")
            send(chat_id, "❌ 일치하는 코드가 없습니다. 앱에서 코드를 다시 확인해주세요.")

    if max_id != offset:
        save_offset(max_id)
        print(f"offset 갱신: {offset} → {max_id}")


if __name__ == "__main__":
    main()
