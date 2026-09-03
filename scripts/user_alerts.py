#!/usr/bin/env python3
"""
사용자별 목표가 알림.

Supabase watchlist에서 목표가가 설정된 행을 모두 읽고,
data/market.json의 현재가와 비교해 조건을 만족하면
그 사용자의 텔레그램 대화방으로 알림을 보낸다.
한 번 발송한 알림은 triggered=true로 표시해 중복 발송을 막는다.
"""
import json
import os
import sys
import time
from pathlib import Path

import requests

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()

MARKET_FILE = Path("data/market.json")

HEADERS = {
    "apikey": SERVICE_KEY,
    "Authorization": f"Bearer {SERVICE_KEY}",
    "Content-Type": "application/json",
}


def fetch_rows(table, params):
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/{table}", headers=HEADERS, params=params, timeout=30
    )
    r.raise_for_status()
    return r.json()


def mark_triggered(row_id):
    requests.patch(
        f"{SUPABASE_URL}/rest/v1/watchlist",
        headers=HEADERS,
        params={"id": f"eq.{row_id}"},
        json={"triggered": True},
        timeout=20,
    )


def send(chat_id, text):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=20,
        )
        ok = r.json().get("ok", False)
        if not ok:
            print(f"  ! 발송 실패 {chat_id}: {r.text[:150]}")
        return ok
    except Exception as e:
        print(f"  ! 발송 예외 {chat_id}: {e}")
        return False


def main():
    if not (BOT_TOKEN and SUPABASE_URL and SERVICE_KEY):
        print("환경변수 누락")
        sys.exit(0)

    if not MARKET_FILE.exists():
        print("market.json 없음 - 건너뜀")
        sys.exit(0)

    market = json.loads(MARKET_FILE.read_text())
    stocks = market.get("stocks", {})
    if not stocks:
        print("시세 데이터 비어있음 - 건너뜀")
        sys.exit(0)

    # 목표가가 있고 아직 발송되지 않은 알림만
    rows = fetch_rows(
        "watchlist",
        {
            "select": "id,user_id,code,name,target,direction,triggered",
            "target": "not.is.null",
            "triggered": "is.false",
        },
    )
    if not rows:
        print("대기 중인 알림 없음")
        return

    # 사용자 → chat_id 매핑
    profiles = fetch_rows(
        "profiles",
        {"select": "id,telegram_chat_id", "telegram_chat_id": "not.is.null"},
    )
    chat_of = {p["id"]: p["telegram_chat_id"] for p in profiles}
    print(f"알림 대기 {len(rows)}건 · 텔레그램 연결 사용자 {len(chat_of)}명")

    sent = 0
    for row in rows:
        chat_id = chat_of.get(row["user_id"])
        if not chat_id:
            continue

        s = stocks.get(row["code"])
        if not s or not s.get("price"):
            continue

        price = s["price"]
        target = float(row["target"])
        direction = row.get("direction") or "above"

        hit = price >= target if direction == "above" else price <= target
        if not hit:
            continue

        arrow = "🔺" if direction == "above" else "🔻"
        change = s.get("change", 0)
        text = (
            f"{arrow} <b>{row.get('name') or row['code']}</b> 목표가 도달\n\n"
            f"현재가 <b>{price:,}원</b> ({change:+.2f}%)\n"
            f"목표가 {target:,.0f}원 {'이상' if direction == 'above' else '이하'}\n\n"
            f"<a href=\"https://finance.naver.com/item/main.naver?code={row['code']}\">네이버 금융에서 보기</a>"
        )

        if send(chat_id, text):
            mark_triggered(row["id"])
            sent += 1
            time.sleep(0.05)  # 텔레그램 초당 30건 제한 회피

    print(f"발송 완료: {sent}건")


if __name__ == "__main__":
    main()
