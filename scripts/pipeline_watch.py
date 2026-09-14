#!/usr/bin/env python3
"""오늘 돌았어야 할 것이 안 돌았으면 텔레그램으로 알린다.

GitHub 은 예약 실행을 자주 버린다. 2026-09-14 에는 Social Post 네 번이
전부 빠졌고, 스크리너도 안 돌아 데이터가 하루 전에 멈춰 있었다. 그런데
**아무 신호가 없었다.** 실패하면 메일이라도 오지만 아예 안 돌면 조용하다.
데이터 날짜를 사람이 직접 열어봐야 알 수 있었다.

그래서 결과만 본다. 워크플로가 돌았는지가 아니라 **나왔어야 할 파일이
나왔는지**를 본다. 실행이 성공해도 빈손으로 끝날 수 있으므로 결과를 보는
편이 맞다.

휴장일을 실패로 착각하지 않는 방법:
  market.json 의 trading_day 를 기준으로 삼는다. 장이 안 섰으면 이 값이
  오늘로 안 바뀌므로 조용히 넘어간다. 장이 섰는데 아래 파일들만 예전
  날짜에 머물러 있으면 그건 안 돈 것이다. 달력 날짜로 재면 추석마다
  헛알림이 온다.

**이 감시 자체도 예약 실행이라 같이 빠질 수 있다.** 그래서 크론을 저녁에
여러 번 걸어 두고, 같은 내용을 하루에 한 번만 보낸다. 네 번 중 한 번만
떠도 알림은 간다.

사용:
  python scripts/pipeline_watch.py
  python scripts/pipeline_watch.py --dry-run   보내지 않고 본문만
"""
import argparse
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pytz  # noqa: E402

from common import send_message  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

ROOT = Path(__file__).resolve().parents[1]
KST = pytz.timezone("Asia/Seoul")
STATE = ROOT / "data" / "watchdog_state.json"
ACTIONS = "https://github.com/clarm3126-prog/richman/actions"

# 장이 선 날 나와야 하는 파일들. (파일, 화면에서 부르는 이름)
DOWNSTREAM = [
    ("screener_results.json", "미너비니 스크리너"),
    ("momentum_results.json", "모멘텀 스크리너"),
    ("exit_signals.json", "매도 신호"),
]

# 평일에 나가야 하는 쓰레드 글. social-post.yml 의 크론과 맞춰 둔다.
EXPECTED_POSTS = 4


def load(name):
    p = ROOT / "data" / name
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def day_of(d):
    """trading_day 를 YYYY-MM-DD 로. 20260914 와 2026-09-14 둘 다 온다."""
    v = str((d or {}).get("trading_day") or "")
    if len(v) == 8 and v.isdigit():
        return f"{v[:4]}-{v[4:6]}-{v[6:]}"
    return v


def check():
    """문제 목록과 기준일을 돌려준다."""
    today = datetime.now(KST).strftime("%Y-%m-%d")
    market = load("market.json")
    problems = []

    if market is None:
        return today, ["시세 파일이 없습니다 (Fetch Prices)"], today

    ref = day_of(market)
    if ref != today:
        # 장이 안 선 날이다. 주말·공휴일이므로 아무것도 안 나온 게 맞다.
        # 다만 시세 자체가 며칠째 멈춰 있으면 그건 따로 봐야 한다.
        return today, [], ref

    for fname, label in DOWNSTREAM:
        d = load(fname)
        if d is None:
            problems.append(f"{label} — 파일 없음")
            continue
        got = day_of(d)
        if got != ref:
            problems.append(f"{label} — {got or '날짜 없음'} 에 멈춤")

    state = load("social/state.json") or {}
    sent = [p for p in state.get("posts", []) if p.get("date") == today]
    if len(sent) < EXPECTED_POSTS:
        problems.append(
            f"쓰레드 글 {len(sent)}건 — 평일에는 {EXPECTED_POSTS}건이 나가야 합니다"
        )
    return today, problems, ref


def already_sent(today, problems):
    """같은 내용을 오늘 이미 보냈으면 True.

    크론을 여러 번 걸어 두었으므로, 안 그러면 저녁마다 같은 알림이
    네 번 온다. 알림이 시끄러우면 결국 안 보게 된다.
    """
    sig = hashlib.sha1("|".join(problems).encode("utf-8")).hexdigest()[:12]
    old = load("watchdog_state.json") or {}
    if old.get("date") == today and old.get("sig") == sig:
        return True
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(
        json.dumps({"date": today, "sig": sig, "problems": problems},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    today, problems, ref = check()
    if not problems:
        if ref != today:
            print(f"장이 안 선 날입니다 (최근 거래일 {ref}) - 넘어감")
        else:
            print(f"{today} 정상 - 알릴 것 없음")
        return 0

    lines = [f"⚠️ {today} 안 돈 것이 있습니다", ""]
    lines += [f"· {p}" for p in problems]
    lines += ["", "예약 실행이 빠졌을 수 있습니다.", "수동 실행:", ACTIONS]
    text = "\n".join(lines)

    print(text)
    if args.dry_run:
        print("\n(--dry-run 이라 보내지 않음)")
        return 0

    if already_sent(today, problems):
        print("\n같은 내용을 오늘 이미 보냈습니다 - 넘어감")
        return 0

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        print("\nTELEGRAM_BOT_TOKEN/CHAT_ID 가 없어 보내지 못했습니다")
        return 0

    ok, msg = send_message(token, chat_id, text, parse_mode=None)
    print(f"\n알림 발송: {'성공' if ok else msg}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
