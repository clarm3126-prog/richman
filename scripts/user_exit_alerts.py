#!/usr/bin/env python3
"""
사용자별 보유종목 매도 시그널 알림.

Supabase watchlist에서 owned=true + 매입가가 있는 종목을 전 사용자분 읽고,
exit_signals.py의 평가 함수를 그대로 재사용해 매도 시그널을 판정한다.
critical / warning 신호만 해당 사용자의 텔레그램으로 발송하며,
같은 종목·같은 신호는 하루 한 번만 보낸다.

장 마감 후 하루 1회 실행 (exit-signals 워크플로우 뒤).
"""
import os
import sys
import time
import traceback
from collections import defaultdict
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from screener import fetch_all_stock_history  # noqa: E402
from exit_signals import evaluate_exit_signals  # noqa: E402

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()

HEADERS = {
    "apikey": SERVICE_KEY,
    "Authorization": f"Bearer {SERVICE_KEY}",
    "Content-Type": "application/json",
}

SEV_ICON = {"critical": "🛑", "warning": "⚠️"}


def fetch_rows(table, params):
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/{table}", headers=HEADERS, params=params, timeout=30
    )
    r.raise_for_status()
    return r.json()


def claim_alert(user_id, code, sig_type):
    """오늘 이 신호를 아직 안 보냈으면 기록하고 True. 이미 보냈으면 False."""
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/exit_alerts_sent",
        headers={**HEADERS, "Prefer": "resolution=ignore-duplicates,return=representation"},
        json={"user_id": user_id, "code": code, "sig_type": sig_type},
        timeout=20,
    )
    if r.status_code >= 300:
        print(f"  ! 기록 실패 {r.status_code}: {r.text[:150]}")
        return False
    return bool(r.json())  # 중복이면 빈 배열


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

    # 1. 보유 종목 (매입가 등록된 것만)
    rows = fetch_rows(
        "watchlist",
        {
            "select": "user_id,code,name,entry_price",
            "owned": "is.true",
            "entry_price": "not.is.null",
        },
    )
    if not rows:
        print("보유 종목 없음")
        return

    # 2. 텔레그램 연결된 사용자만 대상
    profiles = fetch_rows(
        "profiles",
        {"select": "id,telegram_chat_id", "telegram_chat_id": "not.is.null"},
    )
    chat_of = {p["id"]: p["telegram_chat_id"] for p in profiles}
    rows = [r for r in rows if r["user_id"] in chat_of]
    if not rows:
        print("텔레그램 연결된 보유 종목 없음")
        return

    codes = sorted({r["code"] for r in rows})
    print(f"보유 종목 {len(rows)}건 · 고유 종목 {len(codes)}개 · 사용자 {len(set(r['user_id'] for r in rows))}명")

    # 3. OHLC 한 번만 fetch (사용자가 겹쳐도 재사용)
    histories = fetch_all_stock_history(codes, days=252)
    print(f"  OHLC 확보: {len(histories)}/{len(codes)}")

    # 4. 사용자별 메시지 조립
    per_user = defaultdict(list)
    for row in rows:
        history = histories.get(row["code"])
        if not history:
            continue
        try:
            res = evaluate_exit_signals(row["code"], history, row["entry_price"])
        except Exception as e:
            print(f"  ! 평가 실패 {row['code']}: {e}")
            continue
        if not res or not res.get("eligible"):
            continue

        for sig in res.get("signals", []):
            if sig["severity"] not in ("critical", "warning"):
                continue
            if not claim_alert(row["user_id"], row["code"], sig["type"]):
                continue  # 오늘 이미 보냄
            per_user[row["user_id"]].append(
                {
                    "name": row.get("name") or row["code"],
                    "code": row["code"],
                    "severity": sig["severity"],
                    "label": sig["label"],
                    "detail": sig["detail"],
                    "return_pct": res.get("return_pct"),
                    "price": res.get("current_price"),
                }
            )

    if not per_user:
        print("발송할 신규 시그널 없음")
        return

    # 5. 사용자당 한 통으로 묶어 발송
    sent = 0
    for user_id, items in per_user.items():
        items.sort(key=lambda x: 0 if x["severity"] == "critical" else 1)
        lines = ["📉 <b>보유 종목 매도 시그널</b>\n"]
        for it in items:
            icon = SEV_ICON.get(it["severity"], "ℹ️")
            ret = f" ({it['return_pct']:+.1f}%)" if it.get("return_pct") is not None else ""
            lines.append(f"{icon} <b>{it['name']}</b>{ret}")
            lines.append(f"   {it['label']}")
            lines.append(f"   <i>{it['detail']}</i>\n")
        lines.append("판단은 본인 기준으로 하세요. 자동 매매가 아닙니다.")
        if send(chat_of[user_id], "\n".join(lines)):
            sent += 1
            time.sleep(0.05)

    print(f"발송 완료: {sent}명")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
