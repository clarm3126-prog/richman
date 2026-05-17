#!/usr/bin/env python3
"""역사적 신고가(ATH) + 20일 평균 거래량 캐시 — 주간 1회.

거래량 동반 역사적 신고가 돌파 감지(fetch_prices.py)에 사용.

방법:
- 거래대금 상위 ~1800 종목의 장기 일봉(2015~) fetch
- 각 종목: 역대 최고가(고가 기준) + 20일 평균 거래량 계산
- data/ath_cache.json 저장

출력: data/ath_cache.json
{
  "updated": "2026-05-13",
  "stocks": {
    "006340": {"ath": 8200, "ath_date": "20210105", "avg_vol_20d": 1500000}
  }
}
"""
import ast
import concurrent.futures
import json
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path

import pytz
import requests

KST = pytz.timezone("Asia/Seoul")
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ATHFetcher/1.0)"}


def fetch_long_history(code):
    """종목의 장기 일봉 (2015~현재). 역대 최고가 산출용.
    front-api/external/chart/domestic/info — Python literal 응답.
    Returns (code, {ath, ath_date, avg_vol_20d}) or (code, None).
    """
    today = datetime.now(KST)
    start = "20150101"  # ~10년 (실질적 역사적 신고가)
    end = today.strftime("%Y%m%d")
    url = "https://m.stock.naver.com/front-api/external/chart/domestic/info"
    params = {
        "symbol": code,
        "requestType": 1,
        "startTime": start,
        "endTime": end,
        "timeframe": "day",
    }
    headers = {**HEADERS, "Referer": "https://m.stock.naver.com/"}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=12)
        if r.status_code != 200:
            return code, None
        text = r.text.strip()
        if not text:
            return code, None
        data = ast.literal_eval(text)
        if not data or len(data) < 2:
            return code, None
        # data[0] = header, data[1:] = [날짜, 시가, 고가, 저가, 종가, 거래량, 외국인소진율]
        rows = data[1:]
        ath = 0
        ath_date = ""
        volumes = []
        for row in rows:
            if len(row) < 6:
                continue
            try:
                high = float(row[2])
                vol = int(row[5]) if row[5] else 0
            except (ValueError, TypeError):
                continue
            if high > ath:
                ath = high
                ath_date = str(row[0])
            volumes.append(vol)
        if ath <= 0:
            return code, None
        # 최근 20일 평균 거래량
        recent_vols = [v for v in volumes[-20:] if v > 0]
        avg_vol_20d = int(sum(recent_vols) / len(recent_vols)) if recent_vols else 0
        return code, {
            "ath": ath,
            "ath_date": ath_date,
            "avg_vol_20d": avg_vol_20d,
        }
    except Exception:
        return code, None


def fetch_all_ath(codes, max_workers=12):
    print(f"  fetching long history for {len(codes)} stocks...")
    out = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(fetch_long_history, c): c for c in codes}
        done = 0
        for f in concurrent.futures.as_completed(futures):
            code, info = f.result()
            done += 1
            if done % 200 == 0:
                print(f"    ...{done}/{len(codes)} done")
            if info:
                out[code] = info
    print(f"  got ATH data for {len(out)} stocks")
    return out


def main():
    print(f"=== ATH Fetcher — {datetime.now(KST).strftime('%Y-%m-%d %H:%M KST')} ===")

    market_path = Path("data/market.json")
    if not market_path.exists():
        print("ERROR: data/market.json not found.")
        sys.exit(1)
    market = json.loads(market_path.read_text(encoding="utf-8"))
    stocks = market.get("stocks", {})

    # 거래대금 상위 1800개 (사실상 활발한 종목 전체)
    sorted_stocks = sorted(
        [(c, s) for c, s in stocks.items() if s.get("price", 0) > 0 and s.get("volume", 0) > 1000],
        key=lambda x: x[1]["price"] * x[1]["volume"],
        reverse=True,
    )[:1800]
    codes = [c for c, _ in sorted_stocks]
    print(f"  target: top {len(codes)} stocks by trading value")

    ath_data = fetch_all_ath(codes)

    out_path = Path("data/ath_cache.json")
    out_path.write_text(json.dumps({
        "updated": datetime.now(KST).strftime("%Y-%m-%d"),
        "stocks": ath_data,
    }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"\n✅ Saved ath_cache.json: {len(ath_data)} stocks")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
