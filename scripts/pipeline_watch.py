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
세 번 걸어 두었다. 셋 중 하나만 떠도 알림은 간다. 대신 같은 문제를 세 번
알리지 않도록, 오늘 아직 안 알린 문제가 있을 때만 보낸다.

사용:
  python scripts/pipeline_watch.py
  python scripts/pipeline_watch.py --dry-run   보내지 않고 본문만
"""
import argparse
import json
import os
import sys
from datetime import date, datetime
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

# 시세가 이만큼 넘게 멈춰 있으면 휴장이 아니라 고장으로 본다.
# 설·추석 연휴가 닷새까지 가므로 그보다 길게 잡는다.
STALE_DAYS = 6


def _days_since(day):
    """YYYY-MM-DD 에서 오늘까지 며칠. 못 읽으면 크게 돌려 알림이 가게 한다."""
    try:
        y, m, d = (int(x) for x in day.split("-"))
        return (datetime.now(KST).date() - date(y, m, d)).days
    except Exception:
        return 999


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
    if not ref:
        # 파일은 있는데 거래일이 없다. 이걸 휴장일로 넘기면 시세가 깨진
        # 날에 오히려 아무 말도 안 하게 된다. 가장 알아야 할 때 조용해진다.
        return today, ["시세 파일에 거래일이 없습니다 (Fetch Prices)"], today

    if ref != today:
        # 장이 안 선 날이다. 주말·공휴일이므로 아무것도 안 나온 게 맞다.
        #
        # 다만 언제까지고 넘어가면 안 된다. Fetch Prices 가 며칠째 죽어
        # 있어도 "장이 안 섰나 보다" 하고 조용히 지나가기 때문이다.
        # 설·추석 연휴가 닷새까지 가므로 그보다 길어질 때만 알린다.
        if _days_since(ref) > STALE_DAYS:
            return today, [f"시세가 {ref} 에 멈춰 있습니다 (Fetch Prices)"], ref
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


def unreported(today, problems):
    """오늘 아직 안 알린 문제만 추린다.

    크론을 세 번 걸어 두었으므로 그냥 두면 저녁마다 같은 알림이 세 번 온다.
    그렇다고 "내용이 달라지면 보낸다"로 하면, 문제 하나를 고쳤을 때 남은
    것만 적힌 알림이 또 온다. 고쳤는데 알림이 오면 오히려 헷갈린다.
    매도 신호를 고치자마자 실제로 그럴 뻔했다.

    그래서 **새로 생긴 문제가 있을 때만** 보낸다. 줄어든 건 알리지 않는다.
    """
    old = load("watchdog_state.json") or {}
    done = set(old.get("reported") or []) if old.get("date") == today else set()
    return [p for p in problems if p not in done]


def remember(today, problems):
    """보낸 것을 적어 둔다. **보내기에 성공한 뒤에만 부른다.**

    보내기 전에 적으면 실패했을 때 다음 크론이 "이미 알렸다"고 넘어가서
    알림이 영영 안 간다. 첫 실행에서 parse_mode 버그로 발송이 400 을
    받았는데 상태는 이미 적힌 뒤였다.
    """
    old = load("watchdog_state.json") or {}
    done = set(old.get("reported") or []) if old.get("date") == today else set()
    done |= set(problems)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(
        json.dumps({"date": today, "reported": sorted(done)},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


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

    if not unreported(today, problems):
        print("\n오늘 이미 알린 것뿐입니다 - 넘어감")
        return 0

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        print("\nTELEGRAM_BOT_TOKEN/CHAT_ID 가 없어 보내지 못했습니다")
        return 0

    ok, msg = send_message(token, chat_id, text, parse_mode=None)
    print(f"\n알림 발송: {'성공' if ok else msg}")
    if ok:
        remember(today, problems)
    return 0


if __name__ == "__main__":
    sys.exit(main())
