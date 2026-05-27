#!/usr/bin/env python3
"""미국주식 스크리너 — 미너비니 / 모멘텀 / 오닐(신고가 돌파).

한국 스크리너의 평가 함수(evaluate_minervini, evaluate_momentum)를 그대로 재사용.
데이터 소스(전부 무료·키 불필요):
- 종목 universe: Wikipedia S&P 500 + Nasdaq-100
- OHLC: Yahoo chart API (query1.finance.yahoo.com/v8/finance/chart)
- 펀더멘털: Yahoo fundamentals-timeseries (분기/연간 매출·영업이익·EPS)
- 벤치마크(RS): ^GSPC (S&P 500 지수)

출력: data/us_results.json
{
  "updated", "trading_day",
  "minervini": {counts, results:[...]},
  "momentum":  {counts, results:[...]},
  "oneil":     {breakouts:[...]}
}

테스트: US_LIMIT=10 python scripts/us_screener.py  (앞 10종목만)
"""
import concurrent.futures
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent))
from screener import evaluate_minervini  # noqa: E402
from momentum_screener import evaluate_momentum  # noqa: E402

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
HEADERS = {"User-Agent": UA}
ATH_MIN_BASE_DAYS = 60  # 역대 고점이 60일+ 오래된 것만 (이미 달리는 종목 제외)


# ================================
# 1. Universe (S&P500 + Nasdaq-100)
# ================================

def _parse_wiki_constituents(url, sym_col_names):
    """위키 표에서 (symbol, name) 추출."""
    out = {}
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        soup = BeautifulSoup(r.text, "html.parser")
        for table in soup.find_all("table", class_="wikitable"):
            head = [th.get_text(strip=True).lower() for th in table.find_all("th")]
            sym_idx = None
            for i, h in enumerate(head):
                if any(c in h for c in sym_col_names):
                    sym_idx = i
                    break
            if sym_idx is None:
                continue
            name_idx = sym_idx + 1
            for row in table.find_all("tr")[1:]:
                cells = row.find_all(["td", "th"])
                if len(cells) <= sym_idx:
                    continue
                sym = cells[sym_idx].get_text(strip=True).replace(".", "-").upper()
                if not sym or not sym.replace("-", "").isalpha():
                    continue
                name = cells[name_idx].get_text(strip=True) if len(cells) > name_idx else sym
                out[sym] = name
            if out:
                break
    except Exception as e:
        print(f"  wiki parse fail {url}: {e}")
    return out


def load_universe():
    """S&P500 + Nasdaq-100 종목. 실패 시 캐시 사용."""
    cache_path = Path("data/us_universe.json")
    merged = {}
    sp = _parse_wiki_constituents(
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", ("symbol", "ticker"))
    ndx = _parse_wiki_constituents(
        "https://en.wikipedia.org/wiki/Nasdaq-100", ("ticker", "symbol"))
    merged.update(sp)
    merged.update(ndx)
    if len(merged) >= 400:
        cache_path.write_text(json.dumps(merged, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(f"  universe: S&P500 {len(sp)} + Nasdaq100 {len(ndx)} → {len(merged)} unique (cached)")
        return merged
    # fallback
    if cache_path.exists():
        merged = json.loads(cache_path.read_text(encoding="utf-8"))
        print(f"  universe: wiki fetch 부족 → 캐시 사용 ({len(merged)})")
        return merged
    print(f"  universe: 실패 — {len(merged)}개만 확보")
    return merged


# ================================
# 2. Yahoo OHLC + 펀더멘털
# ================================

def fetch_ohlc(symbol, rng="10y"):
    """Yahoo chart API. Returns (history list, current_price, change_pct) or (None, ..)."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {"range": rng, "interval": "1d"}
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return None, 0, 0
        data = r.json()
        res = (data.get("chart", {}).get("result") or [None])[0]
        if not res:
            return None, 0, 0
        ts = res.get("timestamp") or []
        q = (res.get("indicators", {}).get("quote") or [{}])[0]
        opens, highs, lows, closes, vols = (
            q.get("open"), q.get("high"), q.get("low"), q.get("close"), q.get("volume"))
        if not closes:
            return None, 0, 0
        hist = []
        for i in range(len(ts)):
            o, h, l, c, v = opens[i], highs[i], lows[i], closes[i], vols[i]
            if c is None or o is None or h is None or l is None:
                continue
            d = datetime.fromtimestamp(ts[i], tz=timezone.utc).strftime("%Y%m%d")
            hist.append({"date": d, "open": float(o), "high": float(h),
                         "low": float(l), "close": float(c), "volume": int(v or 0)})
        if len(hist) < 2:
            return None, 0, 0
        cur = hist[-1]["close"]
        prev = hist[-2]["close"]
        change = round((cur - prev) / prev * 100, 2) if prev else 0
        return hist, cur, change
    except Exception:
        return None, 0, 0


def _q_key(as_of_date):
    """'2026-03-31' → ('2026_1Q', year)."""
    try:
        y, m, _ = as_of_date.split("-")
        q = (int(m) - 1) // 3 + 1
        return f"{y}_{q}Q", int(y)
    except Exception:
        return None, None


def fetch_fundamentals(symbol):
    """Yahoo fundamentals-timeseries → 한국 screener financials 포맷.
    {"quarters": {"2026_1Q": {EPS, 매출액, 영업이익, 영업이익률}, "2025_Y": {...}}}
    """
    types = ",".join([
        "quarterlyTotalRevenue", "quarterlyOperatingIncome", "quarterlyDilutedEPS",
        "annualTotalRevenue", "annualOperatingIncome",
    ])
    url = f"https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/{symbol}"
    params = {"symbol": symbol, "type": types,
              "period1": 1577836800, "period2": int(time.time()) + 86400, "merge": "false"}
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return None
        results = r.json().get("timeseries", {}).get("result") or []
        # type → {date: value}
        series = {}
        for res in results:
            t = (res.get("meta", {}).get("type") or [None])[0]
            if not t or t not in res:
                continue
            dd = {}
            for pt in res[t]:
                if not pt:
                    continue
                rv = (pt.get("reportedValue") or {}).get("raw")
                ao = pt.get("asOfDate")
                if rv is not None and ao:
                    dd[ao] = rv
            series[t] = dd
        if not series:
            return None
        quarters = {}
        # 분기
        q_rev = series.get("quarterlyTotalRevenue", {})
        q_op = series.get("quarterlyOperatingIncome", {})
        q_eps = series.get("quarterlyDilutedEPS", {})
        for ao, rev in q_rev.items():
            key, _ = _q_key(ao)
            if not key:
                continue
            op = q_op.get(ao)
            entry = {"매출액": rev, "EPS": q_eps.get(ao)}
            entry["영업이익"] = op if op is not None else 0
            entry["영업이익률"] = round(op / rev * 100, 2) if (op is not None and rev) else 0
            quarters[key] = entry
        # 연간
        a_rev = series.get("annualTotalRevenue", {})
        a_op = series.get("annualOperatingIncome", {})
        for ao, rev in a_rev.items():
            try:
                y = ao.split("-")[0]
            except Exception:
                continue
            op = a_op.get(ao)
            quarters[f"{y}_Y"] = {
                "매출액": rev,
                "영업이익률": round(op / rev * 100, 2) if (op is not None and rev) else 0,
            }
        if not quarters:
            return None
        return {"quarters": quarters}
    except Exception:
        return None


# ================================
# 3. 평가 (한국 함수 재사용)
# ================================

def detect_ath(symbol, name, hist, cur, change):
    """역대 신고가 돌파 (close 기준, 거래량 1.5x+, 베이스 60일+)."""
    if len(hist) < 60:
        return None
    highs = [h["high"] for h in hist]
    prior = highs[:-1]
    ath = max(prior)
    if cur < ath:
        return None
    # ath_date (직전 고점 날짜)
    ath_idx = max(range(len(prior)), key=lambda i: prior[i])
    ath_date = hist[ath_idx]["date"]
    try:
        base_days = (datetime.strptime(hist[-1]["date"], "%Y%m%d")
                     - datetime.strptime(ath_date, "%Y%m%d")).days
    except Exception:
        return None
    if base_days < ATH_MIN_BASE_DAYS:
        return None
    vols = [h["volume"] for h in hist[-21:-1] if h["volume"] > 0]
    avg_vol = sum(vols) / len(vols) if vols else 0
    cur_vol = hist[-1]["volume"]
    vol_ratio = round(cur_vol / avg_vol, 2) if avg_vol > 0 else 0
    if vol_ratio < 1.5:
        return None
    return {
        "code": symbol, "name": name, "market": "US", "price": cur, "change": change,
        "ath": ath, "ath_date": ath_date, "base_days": base_days,
        "volume": cur_vol, "vol_ratio": vol_ratio, "supply_demand": False,
    }


def screen_one(symbol, name, bench_closes):
    """단일 종목 — 3개 스크리너 평가. Returns dict or None."""
    hist, cur, change = fetch_ohlc(symbol)
    if not hist or len(hist) < 220:
        return None
    fin = fetch_fundamentals(symbol)
    base = {"code": symbol, "name": name, "market": "US", "price": cur, "change": change}

    out = {"mv": None, "mom": None, "ath": None}
    # 미너비니
    try:
        mv = evaluate_minervini(symbol, hist, fin, bench_closes)
        if mv.get("eligible"):
            out["mv"] = {**base, **mv}
    except Exception:
        pass
    # 모멘텀 (테마 가중치 없음 → 0)
    try:
        mom = evaluate_momentum(symbol, hist, fin, 0)
        if mom.get("eligible"):
            out["mom"] = {**base, **mom}
    except Exception:
        pass
    # 오닐 신고가
    try:
        ath = detect_ath(symbol, name, hist, cur, change)
        if ath:
            out["ath"] = ath
    except Exception:
        pass
    return out


def main():
    print(f"=== US Screener — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} ===")
    universe = load_universe()
    if not universe:
        print("ERROR: universe 비어있음")
        sys.exit(1)

    limit = int(os.environ.get("US_LIMIT", "0"))
    symbols = list(universe.items())
    if limit > 0:
        symbols = symbols[:limit]
        print(f"  [TEST] limit {limit}")

    # 벤치마크 (S&P500) closes
    bench_hist, _, _ = fetch_ohlc("%5EGSPC")
    bench_closes = [h["close"] for h in bench_hist] if bench_hist else []
    print(f"  benchmark ^GSPC: {len(bench_closes)} closes")

    print(f"  screening {len(symbols)} stocks...")
    mv_results, mom_results, ath_results = [], [], []
    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(screen_one, sym, nm, bench_closes): sym for sym, nm in symbols}
        for f in concurrent.futures.as_completed(futures):
            done += 1
            if done % 100 == 0:
                print(f"    ...{done}/{len(symbols)}")
            try:
                res = f.result()
            except Exception:
                continue
            if not res:
                continue
            if res["mv"]:
                mv_results.append(res["mv"])
            if res["mom"]:
                mom_results.append(res["mom"])
            if res["ath"]:
                ath_results.append(res["ath"])

    mv_results.sort(key=lambda x: x.get("total_score", 0), reverse=True)
    mom_results.sort(key=lambda x: x.get("total_score", 0), reverse=True)
    ath_results.sort(key=lambda x: x.get("vol_ratio", 0), reverse=True)

    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out = {
        "updated": now_str,
        "trading_day": today,
        "minervini": {
            "total_evaluated": len(mv_results),
            "minervini_strict_count": sum(1 for r in mv_results if r.get("minervini_strict")),
            "minervini_strong_count": sum(1 for r in mv_results if r.get("minervini_strong")),
            "results": mv_results[:100],
        },
        "momentum": {
            "total_evaluated": len(mom_results),
            "momentum_strong_count": sum(1 for r in mom_results if r.get("momentum_strong")),
            "pre_breakout_count": sum(1 for r in mom_results if r.get("pre_breakout")),
            "results": mom_results[:100],
        },
        "oneil": {"trading_day": today, "close": ath_results, "intraday": []},
    }
    Path("data").mkdir(exist_ok=True)
    Path("data/us_results.json").write_text(
        json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"\n✅ Saved us_results.json")
    print(f"  미너비니: 엄격 {out['minervini']['minervini_strict_count']} · "
          f"우량 {out['minervini']['minervini_strong_count']} (평가 {len(mv_results)})")
    print(f"  모멘텀: 강세 {out['momentum']['momentum_strong_count']} · "
          f"사전 {out['momentum']['pre_breakout_count']} (평가 {len(mom_results)})")
    print(f"  오닐 신고가 돌파: {len(ath_results)}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
