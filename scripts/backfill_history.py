#!/usr/bin/env python3
"""과거 N일치 테마/업종 강도 ranking을 역산해서 data/history/ 백필.

방법:
1. Naver 모든 테마 + 업종 목록 가져오기
2. 각 테마/업종의 현재 멤버 종목 fetch (모든 항목)
3. 모든 종목의 N일치 일봉 OHLC fetch (병렬)
4. 각 날짜별로 멤버 종목 평균 등락률 → 테마/업종 ranking
5. data/history/{YYYYMMDD}.json 으로 저장

주의: 멤버는 "현재 시점" 기준이라 과거 데이터는 약간 부정확할 수 있음.
"""
import concurrent.futures
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

import requests

# 같은 디렉토리의 fetch_prices에서 함수 재사용
sys.path.insert(0, str(Path(__file__).parent))
from fetch_prices import (
    KST, HEADERS,
    fetch_naver_themes,
    fetch_naver_industries,
)


def enrich_all_with_stocks(items, kind):
    """모든 항목에 멤버 종목 추가 (전체 N개)."""
    print(f"  enriching all {len(items)} {kind}s with member stocks (병렬)...")

    def fetch_one(item):
        no = item.get("no")
        if not no:
            return item
        base = "industry" if kind == "industry" else "theme"
        url = f"https://m.stock.naver.com/api/stocks/{base}/{no}?page=1&pageSize=50"
        headers = {**HEADERS, "Referer": "https://m.stock.naver.com/"}
        try:
            r = requests.get(url, headers=headers, timeout=8)
            if r.status_code == 200:
                data = r.json()
                item["stocks"] = [
                    {"code": str(s.get("itemCode")).zfill(6), "name": s.get("stockName")}
                    for s in data.get("stocks", [])
                    if s.get("itemCode") and s.get("stockName")
                ]
            else:
                item["stocks"] = []
        except Exception:
            item["stocks"] = []
        return item

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        list(ex.map(fetch_one, items))


def fetch_stock_history(code, days=60):
    """종목 일봉 OHLC."""
    url = f"https://api.stock.naver.com/chart/domestic/item/{code}?periodType=dayCandle&count={days}"
    headers = {**HEADERS, "Referer": "https://stock.naver.com/"}
    try:
        r = requests.get(url, headers=headers, timeout=8)
        if r.status_code != 200:
            return code, []
        data = r.json()
        out = []
        for p in data.get("priceInfos", []):
            d = p.get("localDate")
            close = p.get("closePrice")
            if d and close is not None:
                try:
                    out.append({"date": str(d), "close": float(close)})
                except (ValueError, TypeError):
                    pass
        return code, out
    except Exception:
        return code, []


def fetch_all_stocks_history(codes, days=60):
    """모든 종목의 일봉 OHLC (병렬)."""
    print(f"  fetching {days}-day history for {len(codes)} stocks (병렬 12)...")
    histories = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as ex:
        futures = {ex.submit(fetch_stock_history, c, days): c for c in codes}
        done = 0
        for f in concurrent.futures.as_completed(futures):
            code, history = f.result()
            done += 1
            if done % 500 == 0:
                print(f"    ...{done}/{len(codes)} done")
            if history:
                histories[code] = history
    print(f"  got history for {len(histories)} stocks")
    return histories


def compute_daily_changes(stocks_history):
    """각 날짜별 종목 등락률 계산. {date: {code: change_pct}}"""
    by_date = {}
    for code, history in stocks_history.items():
        for h in history:
            d = h.get("date")
            close = h.get("close", 0)
            if d and close > 0:
                by_date.setdefault(d, {})[code] = close
    sorted_dates = sorted(by_date.keys())
    if len(sorted_dates) < 2:
        return {}
    daily = {}
    for i in range(1, len(sorted_dates)):
        prev_date = sorted_dates[i - 1]
        cur_date = sorted_dates[i]
        prev_prices = by_date[prev_date]
        cur_prices = by_date[cur_date]
        changes = {}
        for code, cur_close in cur_prices.items():
            prev_close = prev_prices.get(code)
            if prev_close and prev_close > 0:
                changes[code] = (cur_close - prev_close) / prev_close * 100
        daily[cur_date] = changes
    return daily


def compute_ranking(items, changes):
    """테마/업종 평균 등락률 ranking.

    Outlier 처리:
    1. Korean daily limit ±30% 캡 (그 이상은 데이터 오류로 간주)
       - 상장폐지/스톡스플릿/거래정지 등으로 인한 가격 왜곡 방어
    2. 멤버 5개 이상이면 상하위 10% 제거 후 평균 (trimmed mean)
       - 더 안정적인 추세 추정
    """
    rankings = []
    for item in items:
        members = item.get("stocks", [])
        if not members:
            continue
        raw_changes = [
            changes[s["code"]]
            for s in members
            if s.get("code") in changes
        ]
        if not raw_changes:
            continue
        # 1. ±30% cap (한국 일일 상한)
        capped = [max(-30.0, min(30.0, c)) for c in raw_changes]
        # 2. trimmed mean (5개+ 멤버 시 상하위 10% 제거)
        if len(capped) >= 5:
            sorted_c = sorted(capped)
            trim_n = max(1, len(sorted_c) // 10)
            trimmed = sorted_c[trim_n:-trim_n] if len(sorted_c) > 2 * trim_n else sorted_c
            avg = sum(trimmed) / len(trimmed)
        else:
            avg = sum(capped) / len(capped)
        # rise/fall은 capped 기준
        rise = sum(1 for c in capped if c > 0)
        fall = sum(1 for c in capped if c < 0)
        rankings.append({
            "no": item.get("no"),
            "name": item.get("name"),
            "change": round(avg, 2),
            "rise": rise,
            "flat": len(capped) - rise - fall,
            "fall": fall,
        })
    rankings.sort(key=lambda x: x["change"], reverse=True)
    return rankings


def save_history(date, themes_ranking, industries_ranking, overwrite=False):
    """data/history/{date}.json 저장. 이미 있으면 (overwrite=False) skip."""
    hist_dir = Path("data/history")
    hist_dir.mkdir(parents=True, exist_ok=True)
    path = hist_dir / f"{date}.json"
    if path.exists() and not overwrite:
        return False
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "updated": f"BACKFILLED {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')}",
            "trading_day": date,
            "themes": themes_ranking[:100],   # top 100 저장
            "industries": industries_ranking[:50],
        }, f, ensure_ascii=False, separators=(",", ":"))
    return True


def update_index():
    hist_dir = Path("data/history")
    dates = sorted(
        [f.stem for f in hist_dir.glob("*.json") if f.stem != "index"],
        reverse=True,
    )
    with open(hist_dir / "index.json", "w", encoding="utf-8") as f:
        json.dump({"dates": dates}, f)
    print(f"  index updated: {len(dates)} dates total")


def main():
    days = 60
    overwrite = False
    if len(sys.argv) > 1:
        try:
            days = int(sys.argv[1])
        except ValueError:
            pass
    if "--overwrite" in sys.argv:
        overwrite = True

    print(f"=== Backfill {days} days ===")
    print(f"Run time: {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S KST')}")

    print("Step 1: Naver themes + industries 목록...")
    themes = fetch_naver_themes()
    industries = fetch_naver_industries()
    print(f"  themes: {len(themes)}, industries: {len(industries)}")

    print("Step 2: 모든 테마 멤버 종목 fetch (이게 가장 오래 걸림)...")
    enrich_all_with_stocks(themes, "theme")

    print("Step 3: 모든 업종 멤버 종목 fetch...")
    enrich_all_with_stocks(industries, "industry")

    # 모든 unique 종목 코드 수집
    all_codes = set()
    for t in themes:
        for s in t.get("stocks", []):
            if s.get("code"):
                all_codes.add(s["code"])
    for i in industries:
        for s in i.get("stocks", []):
            if s.get("code"):
                all_codes.add(s["code"])
    print(f"Step 4: {len(all_codes)}개 unique 종목의 일봉 fetch...")
    stocks_history = fetch_all_stocks_history(list(all_codes), days)

    print("Step 5: 일별 등락률 계산...")
    daily_changes = compute_daily_changes(stocks_history)
    print(f"  computed {len(daily_changes)} day changes")

    print("Step 6: 일별 테마/업종 ranking + 파일 저장...")
    saved = 0
    skipped = 0
    for date, changes in sorted(daily_changes.items()):
        themes_ranking = compute_ranking(themes, changes)
        industries_ranking = compute_ranking(industries, changes)
        if save_history(date, themes_ranking, industries_ranking, overwrite):
            saved += 1
            if saved <= 3 or saved % 10 == 0:
                print(f"  saved {date}: {len(themes_ranking)} themes, {len(industries_ranking)} industries")
        else:
            skipped += 1

    update_index()
    print(f"\n✅ Done. Saved {saved} new files, skipped {skipped} existing.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
