#!/usr/bin/env python3
"""손절선을 몇 %로 잡아야 하나 — 과거 픽에 손절선만 바꿔가며 대입한다.

"저는 -10%로 잡는데 -7%가 나을까요?"라는 질문을 받아서 만들었다. 의견으로
답하는 것보다 같은 데이터에 숫자만 바꿔 넣어 보는 쪽이 낫다.

backtest.py의 apply_rules()를 그대로 쓴다. 트레일링 규칙(+20% MA21,
+50% MA50)은 건드리지 않고 손절선만 바꾼다. 그래야 차이가 손절선 때문임이
분명해진다.

**결과는 표본 기간에 크게 휘둘린다.** 시장이 빠지는 구간에서는 좁게 자를수록
유리한 것이 당연하다. 숫자를 인용할 때 그 기간이 어땠는지 같이 말하지 않으면
"무조건 좁은 게 낫다"로 읽힌다.

아무것도 저장하지 않는다. 화면에 표만 찍는다.

사용:
  python scripts/stop_sensitivity.py
"""
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import backtest as bt  # noqa: E402

# 견줘 볼 손절선. None은 손절을 아예 걸지 않은 경우다.
LEVELS = [-5, -7, -10, -12, -15, None]
CATEGORY = ("minervini_strong", lambda r: r.get("minervini_strong"))
DAYS = 30


def main():
    snaps = bt.load_history_files("screener_results_history")
    if not snaps:
        raise SystemExit("스냅샷이 없습니다. 먼저 스크리너를 며칠 돌리세요.")

    picks = bt.evaluate_picks(snaps, CATEGORY[1], days_forward=DAYS) or []
    codes = sorted({p["code"] for p in picks})
    print(f"{CATEGORY[0]} · 픽 {len(picks)}건 · 종목 {len(codes)}개 · {DAYS}거래일 보유")
    histories = bt.fetch_all_stock_history(codes, days=bt.HISTORY_DAYS)

    # 픽마다 (일봉, 매수 인덱스, 매도 인덱스)를 한 번만 찾아 둔다.
    rows = []
    for p in picks:
        h = histories.get(p["code"]) or []
        ei = next(
            (i for i, x in enumerate(h)
             if (x.get("date") or "").replace("-", "") == p["date"]),
            None,
        )
        if ei is None:
            continue
        ti = ei + DAYS
        if ti < len(h):
            rows.append((h, ei, ti))
    print(f"평가 가능한 픽 {len(rows)}건\n")

    original = bt.STOP_PCT
    try:
        print(f"{'손절선':>8} {'평균':>9} {'중간값':>9} {'승률':>8} {'최악':>9} {'손절로 끝':>10}")
        for level in LEVELS:
            # 손절을 안 건 경우는 닿을 수 없는 값을 넣어 같은 코드를 태운다.
            bt.STOP_PCT = level if level is not None else -10_000
            out, cut = [], 0
            for h, ei, ti in rows:
                r = bt.apply_rules(h, ei, ti)
                if not r:
                    continue
                out.append(r[0])
                if r[2] == "손절":
                    cut += 1
            if not out:
                continue
            n = len(out)
            label = f"{level}%" if level is not None else "안 걸면"
            print(f"{label:>8} {sum(out) / n:>8.2f}% {statistics.median(out):>8.2f}% "
                  f"{sum(1 for x in out if x > 0) / n * 100:>7.1f}% "
                  f"{min(out):>8.2f}% {cut / n * 100:>9.1f}%")
    finally:
        bt.STOP_PCT = original

    print("\n표본 기간이 어땠는지를 같이 보지 않으면 이 표는 오해를 부릅니다.")


if __name__ == "__main__":
    main()
