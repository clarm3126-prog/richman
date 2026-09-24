#!/usr/bin/env python3
"""추세 조건의 50일선을 60일선으로 바꾸면 무엇이 달라지는지 센다.

한국에서는 20·60일선을 많이 쓴다. 우리는 미너비니를 따라 20·50 이다.

**매도 규칙**의 트레일링은 이미 재봤다(2026-09-20). 차이가 없었다 —
미너비니 923건에서 승률 16.0% 대 15.7%. 그 규칙은 +50% 넘게 벌었을
때만 쓰이는데 그런 거래가 4.1% 밖에 안 돼서, 표본이 답할 힘이 없었다.

**추세 조건**의 50일선은 그때 못 쟀다. 보관함에 50 기준 통과 여부만
박혀 있어 되돌려 셀 수가 없었다. 그래서 2026-09-20 부터 60 기준 판정도
같이 쌓는다(`data/screener_conditions_history/`).

이 자는 쌓인 것으로 셀 수 있는 것만 센다.

  · 판정이 갈린 종목이 며칠에 몇 개인가
  · 60 으로 바꾸면 통과 종목이 늘어나는가 줄어드는가
  · 수익률까지 견주려면 며칠이 더 필요한가

앞의 둘은 며칠만 쌓여도 읽힌다. 수익률은 다르다. 뽑힌 날부터 30일이
지나야 한 건이 되고, 그런 건이 넉넉히 모여야 한다. 그때까지는 남은
날수만 알려준다. **모자란 표본으로 낸 답은 없느니만 못하다.**

  python scripts/ma_5060_compare.py
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
HIST = ROOT / "data" / "screener_conditions_history"
HORIZON = 30          # 수익률을 재는 기간. backtest 와 같게 둔다.
NEED_DAYS = 40        # 이만큼 쌓여야 이야기를 시작한다


def load():
    """쌓인 하루치들. **못 읽은 것은 없는 것이 아니라 「모른다」다.**

    조용히 빼면 쌓인 날수만 줄고 왜 줄었는지가 안 나온다. 몇 달 뒤에
    답을 보러 왔을 때 "생각보다 적네" 하고 넘어가게 된다.
    """
    out, bad = [], []
    for p in sorted(HIST.glob("2*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            bad.append((p.name, type(e).__name__))
            continue
        if d.get("stocks"):
            out.append(d)
        else:
            bad.append((p.name, "stocks 가 비었음"))
    if bad:
        print("⚠️ 못 읽은 하루치 %d개 — 아래 셈에서 빠졌습니다" % len(bad))
        for name, why in bad:
            print("    %s  (%s)" % (name, why))
        print("")
    return out


def verdict(tt, s60, tt_keys, shadow_keys):
    """추세 8개 중 몇 개를 통과했는지, 50 기준과 60 기준으로 각각 센다.

    50 을 60 으로 바꾸면 여덟 중 둘이 갈린다 — "주가가 50일선 위"와
    "50일선이 150일선 위". 나머지 여섯은 그대로다.
    """
    idx = {k: i for i, k in enumerate(tt_keys)}
    sidx = {k: i for i, k in enumerate(shadow_keys)}

    def bit(s, i):
        return s[i] == "1" if (s and i < len(s)) else False

    # 판정에 드는 여덟 (above_25pct_from_52w_low 는 setup 쪽이라 뺀다)
    scored = ["price_above_ma50", "price_above_ma150", "price_above_ma200",
              "ma50_above_ma150", "ma150_above_ma200", "ma200_uptrend",
              "within_25pct_of_52w_high", "rs_rating_70plus"]
    n50 = sum(bit(tt, idx[k]) for k in scored if k in idx)

    n60 = n50
    swap = [("price_above_ma50", "_shadow_price_above_ma60"),
            ("ma50_above_ma150", "_shadow_ma60_above_ma150")]
    for old, new in swap:
        if old not in idx or new not in sidx:
            continue
        n60 += int(bit(s60, sidx[new])) - int(bit(tt, idx[old]))
    return n50, n60


def main():
    snaps = load()
    if not snaps:
        print("아직 쌓인 게 없습니다. screener 가 한 번 돌면 시작됩니다.")
        print("  (%s)" % HIST)
        return 0

    first, last = snaps[0]["trading_day"], snaps[-1]["trading_day"]
    print("쌓인 날 %d일  (%s ~ %s)" % (len(snaps), first, last))

    tot = diff = up = down = 0
    pass50 = pass60 = 0
    for d in snaps:
        tk = d.get("tt_keys") or []
        sk = d.get("shadow_keys") or []
        f = d.get("fields") or []
        try:
            i_tt, i_s60 = f.index("tt"), f.index("s60")
        except ValueError:
            continue
        for rec in d["stocks"].values():
            tt, s60 = rec[i_tt], rec[i_s60]
            n50, n60 = verdict(tt, s60, tk, sk)
            tot += 1
            if n50 != n60:
                diff += 1
                up += n60 > n50
                down += n60 < n50
            # minervini_strong 의 추세 쪽 문턱은 6 이다
            pass50 += n50 >= 6
            pass60 += n60 >= 6

    if not tot:
        print("읽을 수 있는 기록이 없습니다.")
        return 0

    print()
    print("판정이 갈린 자리 %d건 / %d건 (%.1f%%)" % (diff, tot, diff * 100 / tot))
    print("  60 이 더 후하게 본 것 %d건 · 더 박하게 본 것 %d건" % (up, down))
    print()
    print("추세 6개 이상 통과 (미너비니 문턱)")
    print("  50 기준 %d건" % pass50)
    print("  60 기준 %d건  (%+d)" % (pass60, pass60 - pass50))

    print()
    if len(snaps) < NEED_DAYS:
        # 마지막 날에 뽑힌 종목이 30일을 채우는 날
        try:
            d0 = datetime.strptime(first, "%Y%m%d")
        except ValueError:
            d0 = None
        print("수익률 비교는 아직입니다. %d일 쌓였고 %d일은 있어야 합니다."
              % (len(snaps), NEED_DAYS))
        if d0:
            # 40일이 쌓이는 날이 아니라, 그 40일치가 저마다 30일을 채우는
            # 날이다. 장은 이레에 닷새 열리므로 1.4배로 잡는다.
            when = d0 + timedelta(days=int((NEED_DAYS + HORIZON) * 7 / 5))
            print("  %s 쯤이면 셀 수 있습니다. (거래일 %d일 + 뒤따르는 %d일)"
                  % (when.strftime("%Y-%m-%d"), NEED_DAYS, HORIZON))
        print("  그 전에 내는 답은 없느니만 못합니다.")
    else:
        print("표본이 찼습니다. 이제 수익률까지 견줄 수 있습니다.")
        print("  갈린 종목만 모아 30일 뒤 값을 받아서 재면 됩니다.")
        print("  (scripts/backtest.py 의 evaluate_picks 를 그대로 씁니다)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
