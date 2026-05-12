#!/usr/bin/env python3
"""주간 metadata fetcher — 시가총액, 상장일, 관리종목 / 투자유의 정보.

스크리너의 작전주/펌프 필터에 사용.

출력: data/stock_metadata.json
{
  "updated": "...",
  "stocks": {
    "005930": {"market_cap": 4000000000000, "listed_date": "1975-06-11"},
    ...
  },
  "warning_stocks": ["123456", ...],  // 관리종목/투자유의/거래정지
}
"""
import concurrent.futures
import io
import json
import os
import sys
import time
import traceback
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from pathlib import Path

import pytz
import requests

KST = pytz.timezone("Asia/Seoul")
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; MetadataFetcher/1.0)"}

DART_KEY = os.environ.get("DART_API_KEY", "")
DART_BASE = "https://opendart.fss.or.kr/api"


def fetch_stock_metadata_one(code):
    """단일 종목 metadata (시총, 상장일)."""
    url = f"https://m.stock.naver.com/api/stock/{code}/integration"
    headers = {**HEADERS, "Referer": "https://m.stock.naver.com/"}
    try:
        r = requests.get(url, headers=headers, timeout=8)
        if r.status_code != 200:
            return code, None
        data = r.json()
        # 시가총액 추출
        market_cap = None
        listed_date = None
        # totalInfos에서 시총 찾기
        for info in (data.get("totalInfos") or []):
            code_name = info.get("code", "")
            if code_name == "marketValue":
                v = info.get("value", "").replace(",", "").replace("억원", "").replace("조", "").strip()
                # value: "390조 5,000억원" 형태
                raw = info.get("value", "")
                try:
                    cap = 0
                    if "조" in raw:
                        parts = raw.split("조")
                        cap += int(parts[0].replace(",", "").strip()) * 1_0000_0000_0000
                        rest = parts[1] if len(parts) > 1 else ""
                        if "억" in rest:
                            cap += int(rest.replace(",", "").replace("억원", "").replace("억", "").strip() or 0) * 1_0000_0000
                    elif "억" in raw:
                        cap = int(raw.replace(",", "").replace("억원", "").replace("억", "").strip()) * 1_0000_0000
                    market_cap = cap
                except Exception:
                    pass
            elif code_name == "listedDate":
                listed_date = info.get("value", "").strip()
        return code, {"market_cap": market_cap, "listed_date": listed_date}
    except Exception:
        return code, None


def fetch_all_metadata(codes, max_workers=10):
    """모든 종목 metadata 병렬 fetch."""
    print(f"  fetching metadata for {len(codes)} stocks...")
    out = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(fetch_stock_metadata_one, c): c for c in codes}
        done = 0
        for f in concurrent.futures.as_completed(futures):
            code, meta = f.result()
            done += 1
            if done % 100 == 0:
                print(f"    ...{done}/{len(codes)} done")
            if meta:
                out[code] = meta
    print(f"  got metadata for {len(out)} stocks")
    return out


def fetch_dart_warning_stocks():
    """DART 관리종목 / 투자유의 / 거래정지 종목 조회.
    공시검색 API에서 '관리종목', '투자유의', '거래정지' 키워드로 최근 90일 공시 검색.
    """
    if not DART_KEY:
        print("  no DART_API_KEY (skip warning stocks)")
        return set()
    from datetime import timedelta
    warning = set()
    today = datetime.now(KST)
    bgn_de = (today - timedelta(days=90)).strftime("%Y%m%d")
    end_de = today.strftime("%Y%m%d")
    # 공시 종류: 관리종목 지정/해제 등 — 한 번에 모든 공시 fetch 후 키워드 4개 동시 매칭
    keywords = ("관리종목지정", "투자유의종목지정", "상장폐지", "거래정지", "정리매매", "감리종목", "불성실공시")
    page = 1
    while page <= 10:  # max 10 pages × 100 = 1000 disclosures
        url = f"{DART_BASE}/list.json"
        params = {
            "crtfc_key": DART_KEY,
            "bgn_de": bgn_de,
            "end_de": end_de,
            "page_no": page,
            "page_count": 100,
        }
        try:
            r = requests.get(url, params=params, timeout=10)
            data = r.json()
            if data.get("status") != "000":
                break
            items = data.get("list", [])
            if not items:
                break
            for item in items:
                report_nm = item.get("report_nm", "") or ""
                if any(k in report_nm for k in keywords):
                    stock_code = item.get("stock_code", "")
                    if stock_code and stock_code.isdigit():
                        warning.add(stock_code.zfill(6))
            if len(items) < 100:
                break
            page += 1
            time.sleep(0.05)
        except Exception as e:
            print(f"  warning stocks fetch err page {page}: {e}")
            break
    print(f"  found {len(warning)} warning stocks (관리/투자유의/상장폐지/거래정지)")
    return warning


def main():
    print(f"=== Metadata Fetcher — {datetime.now(KST).strftime('%Y-%m-%d %H:%M KST')} ===")

    market_path = Path("data/market.json")
    if not market_path.exists():
        print("ERROR: data/market.json not found.")
        sys.exit(1)
    market = json.loads(market_path.read_text(encoding="utf-8"))
    stocks = market.get("stocks", {})

    # 거래대금 상위 1500개로 제한
    sorted_stocks = sorted(
        [(c, s) for c, s in stocks.items() if s.get("price", 0) > 0 and s.get("volume", 0) > 1000],
        key=lambda x: x[1]["price"] * x[1]["volume"],
        reverse=True,
    )[:1500]
    codes = [c for c, _ in sorted_stocks]
    print(f"  target: top {len(codes)} stocks by trading value")

    # 기존 캐시 로드
    out_path = Path("data/stock_metadata.json")
    cache = {"stocks": {}, "warning_stocks": []}
    if out_path.exists():
        try:
            cache = json.loads(out_path.read_text(encoding="utf-8"))
            cache.setdefault("stocks", {})
            cache.setdefault("warning_stocks", [])
        except Exception:
            pass

    # 1. metadata fetch (캐시에 없거나 30일 이상 지난 것만)
    today_str = datetime.now(KST).strftime("%Y-%m-%d")
    needs_fetch = []
    for code in codes:
        existing = cache["stocks"].get(code)
        if not existing or not existing.get("listed_date"):
            needs_fetch.append(code)
    print(f"  needs fetch: {len(needs_fetch)} (cached: {len(codes) - len(needs_fetch)})")

    if needs_fetch:
        new_meta = fetch_all_metadata(needs_fetch)
        for code, meta in new_meta.items():
            meta["_fetched_at"] = today_str
            cache["stocks"][code] = meta

    # 시가총액은 항상 최신화 (가격 변동 반영)
    # 1주일에 한 번 정도 전체 refresh
    last_full_refresh = cache.get("_last_full_refresh", "")
    do_full_refresh = last_full_refresh != today_str.split("-")[0] + "-" + today_str.split("-")[1]  # monthly
    if do_full_refresh and len(needs_fetch) < len(codes):
        print(f"  monthly refresh: re-fetching all metadata...")
        refresh_meta = fetch_all_metadata(codes)
        for code, meta in refresh_meta.items():
            meta["_fetched_at"] = today_str
            existing = cache["stocks"].get(code, {})
            existing.update(meta)
            cache["stocks"][code] = existing
        cache["_last_full_refresh"] = today_str.split("-")[0] + "-" + today_str.split("-")[1]

    # 2. DART warning stocks
    print("\n[DART] 관리종목/투자유의/거래정지...")
    warning = fetch_dart_warning_stocks()
    cache["warning_stocks"] = sorted(warning)

    # 3. save
    cache["updated"] = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")
    out_path.write_text(json.dumps(cache, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"\n✅ Saved stock_metadata.json: {len(cache['stocks'])} stocks, {len(cache['warning_stocks'])} warnings")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
