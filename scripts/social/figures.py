#!/usr/bin/env python3
"""대기열 글에 박아 둔 숫자를 발행 직전에 현재 값과 맞춘다.

백테스트는 매일 다시 돈다. 창이 굴러가므로 표본 수도 수익률도 조금씩
움직인다. 그런데 대기열 글은 쓴 날의 숫자를 그대로 들고 2~3주를 기다린다.

작은 차이는 읽는 사람이 모른다. 문제는 **주장이 바뀌는 때**다. 실제로
한 번 있었다.

    쓸 때   조건 -15.2%  vs  코스피 -10.9%   →  4.3%p 뒤짐
    오늘    조건 -14.7%  vs  코스피 -14.5%   →  0.16%p 차이

"조건만 보고 버티면 지수를 못 넘는다"가 글의 요지였는데, 지수가 한 달 만에
따라 내려와 대비가 사라졌다. 글은 그 자리에 멈춰 있었다.

그래서 글마다 `figures:` 에 **어느 값을 어디서 가져왔는지** 적어 두고,
발행 직전에 다시 계산해 본문을 고친다.

    figures:
      bt30.avg: "-14.7%"
      bt30.index: "-14.5%"

적어 둔 문자열이 곧 형식이다. `-14.7%` 라고 적었으면 소수 한 자리에 %를
붙여 견주고, `918` 이라고 적었으면 정수로 견준다. 같은 값을 소수 두 자리로
쓰고 싶으면 `-14.66%` 라고 적으면 된다.

본문에 그 문자열이 **정확히 한 번** 나올 때만 고친다. 여러 번이거나 없으면
고치지 않고 알린다 — 엉뚱한 자리를 건드리는 것보다 낫다.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(name):
    p = ROOT / "data" / name
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def values():
    """키 → 현재 숫자. 없는 키는 빠진 채로 돌려준다."""
    out = {}
    bt = (_load("backtest_stats.json").get("categories") or {}).get("minervini_strong") or {}

    for horizon, prefix in (("30d", "bt30"), ("60d", "bt60")):
        c = bt.get(horizon)
        if not c:
            continue
        out[prefix + ".count"] = c.get("count")
        out[prefix + ".codes"] = c.get("unique_codes")
        out[prefix + ".win"] = c.get("win_rate")
        out[prefix + ".avg"] = c.get("avg_return")
        out[prefix + ".median"] = c.get("median_return")
        out[prefix + ".min"] = c.get("min_return")
        out[prefix + ".bigwin"] = c.get("big_wins_20pct")
        out[prefix + ".bigloss"] = c.get("big_losses_neg10pct")
        idx = ((c.get("period") or {}).get("index") or {})
        out[prefix + ".index"] = idx.get("change_pct")
        out[prefix + ".drawdown"] = idx.get("max_drawdown_pct")
        r = c.get("rules") or {}
        out[prefix + "r.win"] = r.get("win_rate")
        out[prefix + "r.avg"] = r.get("avg_return")
        out[prefix + "r.min"] = r.get("min_return")
        out[prefix + "r.days"] = r.get("avg_days")
        # 점수대별. label 이 한글이라 자리로 잡는다.
        for slot, label in (("bot", "하위 25%"), ("mid", "가운데 50%"), ("top", "상위 25%")):
            for x in (c.get("by_score") or []):
                if x.get("label") == label:
                    out["%s.%s.count" % (prefix, slot)] = x.get("count")
                    out["%s.%s.avg" % (prefix, slot)] = x.get("avg_return")
                    out["%s.%s.bench" % (prefix, slot)] = x.get("benchmark_avg")

    # 조건 통과율. 매일 다시 계산되고 평가 종목 수부터 바뀐다.
    sc = _load("screener_conditions.json")
    stocks = sc.get("stocks") or {}
    tt = sc.get("tt_keys") or []
    if stocks and tt:
        total = len(stocks)
        out["cond.total"] = total
        hit = [0] * len(tt)
        for v in stocks.values():
            bits = str(v.get("tt") or "")
            for i, ch in enumerate(bits[: len(tt)]):
                if ch == "1":
                    hit[i] += 1
        # 화면에 쓰는 이름으로 짧게 건다
        SHORT = {
            "within_25pct_of_52w_high": "high25",
            "above_25pct_from_52w_low": "low25",
            "ma50_above_ma150": "ma50_150",
            "ma150_above_ma200": "ma150_200",
            "ma200_uptrend": "ma200up",
            "price_above_ma50": "ma50",
            "price_above_ma150": "ma150",
            "price_above_ma200": "ma200",
            "rs_rating_70plus": "rs",
        }
        for key, c in zip(tt, hit):
            name = SHORT.get(key, key)
            out["cond.%s.count" % name] = c
            out["cond.%s.pct" % name] = round(c * 100 / total, 1) if total else None

    ss = _load("stop_sensitivity.json")
    for x in (ss.get("levels") or []):
        # "-5%" → stop.5 / "안 걸면" → stop.none
        lab = x.get("label") or ""
        key = "none" if "걸" in lab else lab.strip("-%")
        out["stop.%s.avg" % key] = x.get("avg_return")
        out["stop.%s.win" % key] = x.get("win_rate")
        out["stop.%s.count" % key] = x.get("count")
    return {k: v for k, v in out.items() if v is not None}


def _fmt(value, like):
    """적어 둔 문자열과 같은 모양으로 숫자를 찍는다.

    소수 자리수·퍼센트·천 단위 쉼표를 그대로 따라간다. 그래야 "-14.7%" 라고
    적어 둔 자리에 "-14.66%" 가 들어가 줄이 길어지는 일이 없다.
    """
    pct = like.rstrip().endswith("%")
    body = like.rstrip().rstrip("%")
    dot = body.rfind(".")
    digits = len(body) - dot - 1 if dot >= 0 else 0
    if "," in body:
        s = "{:,.{d}f}".format(float(value), d=digits)
    else:
        s = "{:.{d}f}".format(float(value), d=digits)
    return s + ("%" if pct else "")


def refresh(text, figures):
    """본문의 숫자를 현재 값으로 고친다. (새 본문, 알릴 말 목록)"""
    if not figures:
        return text, []
    cur = values()
    notes = []
    for key, written in figures.items():
        written = str(written)
        if key not in cur:
            notes.append("숫자 '%s' 의 출처 %s 를 못 찾았습니다" % (written, key))
            continue
        now = _fmt(cur[key], written)
        if now == written:
            continue
        n = text.count(written)
        if n != 1:
            notes.append("%s 가 %s → %s 로 바뀌었는데 본문에 %d번 나와 못 고쳤습니다"
                         % (key, written, now, n))
            continue
        text = text.replace(written, now)
        notes.append("%s %s → %s 로 고쳤습니다" % (key, written, now))
    return text, notes
