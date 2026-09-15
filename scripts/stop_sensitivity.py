#!/usr/bin/env python3
"""손절선을 몇 %로 잡아야 하나 — 과거 픽에 손절선만 바꿔가며 대입한다.

"저는 -10%로 잡는데 -7%가 나을까요?"라는 질문을 받아서 만들었다. 의견으로
답하는 것보다 같은 데이터에 숫자만 바꿔 넣어 보는 쪽이 낫다.

backtest.py의 apply_rules()를 그대로 쓴다. 트레일링 규칙(+20% MA20,
+50% MA50)은 건드리지 않고 손절선만 바꾼다. 그래야 차이가 손절선 때문임이
분명해진다.

**결과는 표본 기간에 크게 휘둘린다.** 시장이 빠지는 구간에서는 좁게 자를수록
유리한 것이 당연하다. 숫자를 인용할 때 그 기간이 어땠는지 같이 말하지 않으면
"무조건 좁은 게 낫다"로 읽힌다.

화면에 표를 찍고 data/stop_sensitivity.json 에 같은 내용을 남긴다. 블로그와
카드가 이 숫자를 인용하는데, 매일 표본이 늘어 값이 조금씩 움직인다. 파일로
남겨두지 않으면 인용한 숫자가 언제 것인지 알 수 없다.

표본 기간(period)을 함께 저장한다. 이 표는 기간에 크게 휘둘리므로 숫자만
떼어 쓰면 "무조건 좁은 게 낫다"로 읽힌다.

출력: data/stop_sensitivity.json

사용:
  python scripts/stop_sensitivity.py
"""
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

import pytz

sys.path.insert(0, str(Path(__file__).parent))

import backtest as bt  # noqa: E402

KST = pytz.timezone("Asia/Seoul")

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
    levels_out = []
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
            levels_out.append({
                "stop_pct": level,
                "label": label,
                "count": n,
                "avg_return": round(sum(out) / n, 2),
                "median_return": round(statistics.median(out), 2),
                "win_rate": round(sum(1 for x in out if x > 0) / n * 100, 1),
                "min_return": round(min(out), 2),
                "stopped_out_pct": round(cut / n * 100, 1),
            })
    finally:
        bt.STOP_PCT = original

    print("\n표본 기간이 어땠는지를 같이 보지 않으면 이 표는 오해를 부릅니다.")
    save(levels_out, picks, codes)


def save(levels_out, picks, codes):
    """표와 표본 기간을 파일로 남긴다.

    period는 backtest.py가 쓰는 것과 같은 모양으로 맞춘다. 두 파일을 나란히
    읽을 때 같은 구간을 보고 있는지 바로 드러나게 하려는 것이다.
    """
    dates = sorted({p["date"] for p in picks})
    out = {
        "updated": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        "category": CATEGORY[0],
        "days_forward": DAYS,
        "trailing": "+20% MA20 · +50% MA50 (손절선만 바꾼다)",
        "period": {
            "first_entry": dates[0] if dates else "",
            "last_entry": dates[-1] if dates else "",
            "entry_days": len(dates),
            "codes": len(codes),
        },
        "levels": levels_out,
    }

    # 지수는 backtest.py와 같은 출처(data/market.json)를 쓴다. 화면에 뜨는
    # 지수와 어긋나면 같은 구간을 두고 두 숫자가 달라진다.
    index_closes = {}
    try:
        mk = json.loads(Path("data/market.json").read_text(encoding="utf-8"))
        for row in (mk.get("indices", {}).get("kospi", {}).get("history") or []):
            d = str(row.get("date") or "").replace("-", "")
            if d and row.get("close"):
                index_closes[d] = row["close"]
    except Exception as e:
        print(f"  지수 히스토리를 못 읽었습니다 ({e}) — 기간 지수는 건너뜁니다")
    if index_closes and dates:
        w = bt.index_window(index_closes, dates[0], dates[-1], DAYS)
        if w:
            out["period"]["index"] = w

    path = Path("data/stop_sensitivity.json")
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"저장: {path}")


if __name__ == "__main__":
    main()
