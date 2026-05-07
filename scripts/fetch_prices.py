#!/usr/bin/env python3
"""네이버 금융에서 KOSPI/KOSDAQ 전 종목 시세를 수집해 data/market.json으로 저장."""
import json
import re
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

import pytz
import requests
from bs4 import BeautifulSoup

KST = pytz.timezone("Asia/Seoul")
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9",
}


def parse_int(text):
    text = (text or "").strip().replace(",", "").replace(" ", "")
    if not text or text in ("N/A", "-"):
        return 0
    try:
        return int(text)
    except ValueError:
        return 0


def parse_change(cell):
    """등락률 셀에서 부호 포함 float 반환."""
    text = cell.get_text(strip=True).replace(",", "").replace("%", "").replace(" ", "")
    is_negative = text.startswith("-") or text.startswith("−")
    is_positive = text.startswith("+")
    text_clean = text.lstrip("+-−")
    try:
        val = float(text_clean)
    except ValueError:
        return 0.0
    if val == 0:
        return 0.0
    if is_negative:
        return -abs(val)
    if is_positive:
        return abs(val)
    # 텍스트에 부호 없음 → 클래스/HTML 컬러로 판단
    html = str(cell).lower()
    if "nv" in html or "down" in html or "blue" in html:
        return -abs(val)
    return abs(val)


def fetch_market(sosok):
    """sosok=0: KOSPI, 1: KOSDAQ. 시가총액 페이지를 페이지별로 스크래핑."""
    market = "KOSPI" if sosok == 0 else "KOSDAQ"
    out = {}
    page = 1
    empty_pages = 0
    while page <= 60:
        url = f"https://finance.naver.com/sise/sise_market_sum.naver?sosok={sosok}&page={page}"
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            r.encoding = "euc-kr"
        except Exception as e:
            print(f"  page {page} request failed: {e}")
            break

        soup = BeautifulSoup(r.text, "html.parser")
        rows = soup.select("table.type_2 tr")
        found = 0
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 11:
                continue
            link = row.select_one("a.tltle")
            if not link:
                continue
            name = link.get_text(strip=True)
            m = re.search(r"code=(\d+)", link.get("href", ""))
            if not m:
                continue
            code = m.group(1).zfill(6)
            price = parse_int(cells[2].get_text())
            change = parse_change(cells[4])
            volume = parse_int(cells[9].get_text())
            out[code] = {
                "name": name,
                "market": market,
                "price": price,
                "change": change,
                "volume": volume,
            }
            found += 1

        if found == 0:
            empty_pages += 1
            if empty_pages >= 2:
                break
        else:
            empty_pages = 0
        page += 1
        time.sleep(0.15)

    print(f"  {market}: {len(out)} stocks ({page - 1} pages)")
    return out


def fetch_index(code):
    """code='KOSPI' or 'KOSDAQ'. 지수 현재값과 등락률."""
    url = f"https://finance.naver.com/sise/sise_index.naver?code={code}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.encoding = "euc-kr"
        soup = BeautifulSoup(r.text, "html.parser")
        value_el = soup.select_one("#now_value")
        if not value_el:
            return None
        value_text = value_el.get_text(strip=True).replace(",", "")
        try:
            value = float(value_text)
        except ValueError:
            return None
        change = 0.0
        change_el = soup.select_one("#change_value_and_rate")
        if change_el:
            txt = change_el.get_text(strip=True)
            m = re.search(r"([+-]?\d+\.\d+)\s*%", txt)
            if m:
                change = float(m.group(1))
            html = str(change_el).lower()
            if "ico_down" in html or "blue" in html:
                change = -abs(change)
            elif "ico_up" in html or "red" in html:
                change = abs(change)
        return {"value": value, "change": change}
    except Exception as e:
        print(f"  index {code} fetch failed: {e}")
        return None


def fetch_52w_high_for_stock(code):
    """단일 종목의 52주 최고가 + 오늘 고가 반환."""
    url = f"https://m.stock.naver.com/api/stock/{code}/integration"
    headers = {**HEADERS, "Referer": "https://m.stock.naver.com/"}
    for _ in range(2):
        try:
            r = requests.get(url, headers=headers, timeout=8)
            if r.status_code == 429:
                time.sleep(1.5)
                continue
            if r.status_code != 200:
                return code, None, None
            data = r.json()
            high_52w = None
            today_high = None
            for item in data.get("totalInfos", []):
                ic = item.get("code")
                v = str(item.get("value", "")).replace(",", "")
                if ic == "highPriceOf52Weeks":
                    try:
                        high_52w = int(v)
                    except ValueError:
                        pass
                elif ic == "highPrice":
                    try:
                        today_high = int(v)
                    except ValueError:
                        pass
            return code, high_52w, today_high
        except Exception:
            return code, None, None
    return code, None, None


def find_new_highs(stocks):
    """52주 신고가 종목 찾기. 모바일 API 병렬 호출 후 오늘 고가가 52주 최고가에 도달한 종목 추출."""
    import concurrent.futures
    candidates = [
        code for code, s in stocks.items()
        if s.get("volume", 0) > 5000 and s.get("price", 0) > 0
    ]
    print(f"  Checking {len(candidates)} candidates for 52w high...")

    new_highs = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=15) as ex:
        futures = {ex.submit(fetch_52w_high_for_stock, c): c for c in candidates}
        done = 0
        for f in concurrent.futures.as_completed(futures):
            code, high_52w, today_high = f.result()
            done += 1
            if done % 500 == 0:
                print(f"    ...{done}/{len(candidates)} checked")
            if high_52w and today_high and today_high >= high_52w:
                new_highs.append(code)
    new_highs.sort(key=lambda c: stocks[c].get("change", 0), reverse=True)
    print(f"  Found {len(new_highs)} stocks at 52w high")
    return new_highs


def main():
    print(f"Run time: {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S KST')}")
    print("Fetching Naver Finance market data...")

    stocks = {}
    for sosok in [0, 1]:
        stocks.update(fetch_market(sosok))
    print(f"Total: {len(stocks)} stocks")

    if len(stocks) < 100:
        raise RuntimeError(
            f"수집된 종목이 너무 적음 ({len(stocks)}). "
            "네이버 페이지 구조가 변경되었거나 접근이 차단되었을 수 있음."
        )

    indices = {
        "kospi": fetch_index("KOSPI"),
        "kosdaq": fetch_index("KOSDAQ"),
    }
    print(f"Indices: {indices}")

    new_highs = find_new_highs(stocks)

    out = {
        "updated": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        "trading_day": datetime.now(KST).strftime("%Y%m%d"),
        "indices": indices,
        "stocks": stocks,
        "new_highs": new_highs,
    }

    out_path = Path("data/market.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"Wrote {out_path}: {out_path.stat().st_size:,} bytes")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
