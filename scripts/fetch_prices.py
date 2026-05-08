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


def fetch_naver_industries():
    """네이버 금융 업종 리스트 (등락률·상승/하락 종목수 포함)."""
    out = []
    url = "https://finance.naver.com/sise/sise_group.naver?type=upjong"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.encoding = "euc-kr"
    except Exception as e:
        print(f"  industries fetch failed: {e}")
        return out
    m = re.search(r'<table[^>]*type_1[^>]*>(.*?)</table>', r.text, re.S)
    if not m:
        return out
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', m.group(1), re.S)
    for row in rows:
        nm = re.search(r'no=(\d+)[^>]*>([^<]+)</a>', row)
        if not nm:
            continue
        pct = re.search(r'(red01|nv01)[^>]*>\s*([+\-]?\d+\.\d+)\s*%', row, re.S)
        if pct:
            cls, val = pct.groups()
            change = float(val) if cls == "red01" else -abs(float(val))
        else:
            change = 0.0
        text_tds = []
        for td_m in re.finditer(r'<td[^>]*>(.*?)</td>', row, re.S):
            text_tds.append(re.sub(r'<[^>]+>', ' ', td_m.group(1)).strip())
        def n(i):
            try:
                return int(text_tds[i].replace(",", "").replace("+", "").replace("-", "").strip())
            except (ValueError, IndexError):
                return 0
        total = n(2)
        rise = n(3)
        flat = n(4)
        fall = n(5)
        out.append({
            "no": nm.group(1),
            "name": nm.group(2).strip(),
            "change": change,
            "total": total,
            "rise": rise,
            "flat": flat,
            "fall": fall,
        })
    out.sort(key=lambda i: i["change"], reverse=True)
    print(f"  Naver industries: {len(out)}")
    return out


def enrich_top_industries_with_stocks(industries, top_n=10):
    """상위 N개 네이버 업종에 멤버 종목 리스트 추가."""
    for ind in industries[:top_n]:
        ind_no = ind.get("no")
        if not ind_no:
            continue
        url = f"https://m.stock.naver.com/api/stocks/industry/{ind_no}?page=1&pageSize=50"
        headers = {**HEADERS, "Referer": "https://m.stock.naver.com/"}
        try:
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code != 200:
                ind["stocks"] = []
                continue
            data = r.json()
            stocks = []
            for s in data.get("stocks", []):
                code = s.get("itemCode")
                name = s.get("stockName")
                if code and name:
                    stocks.append({"code": str(code).zfill(6), "name": name})
            ind["stocks"] = stocks
        except Exception as e:
            print(f"  industry {ind_no} stocks fetch failed: {e}")
            ind["stocks"] = []
        time.sleep(0.1)
    print(f"  enriched {top_n} industries with member stocks")


def enrich_top_themes_with_stocks(themes, top_n=10):
    """상위 N개 네이버 테마에 멤버 종목 리스트 추가."""
    for theme in themes[:top_n]:
        theme_no = theme.get("no")
        if not theme_no:
            continue
        url = f"https://m.stock.naver.com/api/stocks/theme/{theme_no}?page=1&pageSize=50"
        headers = {**HEADERS, "Referer": "https://m.stock.naver.com/"}
        try:
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code != 200:
                theme["stocks"] = []
                continue
            data = r.json()
            stocks = []
            for s in data.get("stocks", []):
                code = s.get("itemCode")
                name = s.get("stockName")
                if code and name:
                    stocks.append({"code": str(code).zfill(6), "name": name})
            theme["stocks"] = stocks
        except Exception as e:
            print(f"  theme {theme_no} stocks fetch failed: {e}")
            theme["stocks"] = []
        time.sleep(0.1)
    print(f"  enriched {top_n} themes with member stocks")


def fetch_company_description(code):
    """fnguide에서 기업 개요(bizSummaryContent) 가져오기."""
    url = f"https://comp.fnguide.com/SVO2/asp/SVD_Main.asp?gicode=A{code}&NewMenuID=Y&pGB=1&stkGb=701"
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        html = r.text
        m = re.search(r'<ul[^>]*id="bizSummaryContent"[^>]*>(.*?)</ul>', html, re.S)
        if not m:
            return None
        items = re.findall(r'<li[^>]*>(.*?)</li>', m.group(1), re.S)
        texts = []
        for item in items:
            t = re.sub(r'&nbsp;', ' ', item)
            t = re.sub(r'<[^>]+>', '', t).strip()
            if t:
                texts.append(t)
        return ' '.join(texts) if texts else None
    except Exception:
        return None


def update_descriptions(themes, industries):
    """캐시되지 않은 종목들의 설명을 fnguide에서 수집해 data/descriptions.json에 저장."""
    import concurrent.futures
    desc_path = Path("data/descriptions.json")
    desc_path.parent.mkdir(parents=True, exist_ok=True)
    descriptions = {}
    if desc_path.exists():
        try:
            descriptions = json.loads(desc_path.read_text(encoding="utf-8"))
        except Exception:
            descriptions = {}

    needed = set()
    for t in themes[:10]:
        for s in t.get("stocks", []):
            code = s.get("code")
            if code and code not in descriptions:
                needed.add(code)
    for i in industries[:10]:
        for s in i.get("stocks", []):
            code = s.get("code")
            if code and code not in descriptions:
                needed.add(code)

    if not needed:
        print(f"  descriptions: 0 new (total cached: {len(descriptions)})")
        return

    print(f"  fetching {len(needed)} company descriptions...")
    def fetch(code):
        return code, fetch_company_description(code)
    success = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        for code, desc in ex.map(fetch, needed):
            if desc:
                descriptions[code] = desc
                success += 1

    desc_path.write_text(
        json.dumps(descriptions, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"  descriptions: +{success} new, total cached: {len(descriptions)}")


def save_history(themes, trading_day):
    """일별 테마 강도 데이터를 data/history/{YYYYMMDD}.json 으로 저장하고 index 갱신."""
    if not trading_day or not themes:
        return
    hist_dir = Path("data/history")
    hist_dir.mkdir(parents=True, exist_ok=True)
    today_file = hist_dir / f"{trading_day}.json"
    with open(today_file, "w", encoding="utf-8") as f:
        json.dump({
            "updated": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
            "trading_day": trading_day,
            "themes": themes,
        }, f, ensure_ascii=False, separators=(",", ":"))
    dates = sorted(
        [f.stem for f in hist_dir.glob("*.json") if f.stem != "index"],
        reverse=True,
    )
    with open(hist_dir / "index.json", "w", encoding="utf-8") as f:
        json.dump({"dates": dates}, f)
    print(f"  history saved: {today_file.name} (total {len(dates)} days)")


def fetch_naver_themes():
    """네이버 금융 테마 리스트 (등락률·상승/하락 종목수 포함)."""
    out = []
    seen_nos = set()
    for page in range(1, 15):
        url = f"https://finance.naver.com/sise/theme.naver?page={page}"
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            r.encoding = "euc-kr"
        except Exception as e:
            print(f"  themes page {page} failed: {e}")
            break
        m = re.search(r'<table[^>]*type_1\s+theme[^>]*>(.*?)</table>', r.text, re.S)
        if not m:
            break
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', m.group(1), re.S)
        found = 0
        for row in rows:
            nm = re.search(r'no=(\d+)[^>]*>([^<]+)</a>', row)
            if not nm:
                continue
            theme_no = nm.group(1)
            if theme_no in seen_nos:
                continue
            seen_nos.add(theme_no)
            pct = re.search(r'col_type2.*?(red01|nv01)[^>]*>\s*([+\-]?\d+\.\d+)\s*%', row, re.S)
            if pct:
                cls, val = pct.groups()
                change = float(val) if cls == "red01" else -abs(float(val))
            else:
                change = 0.0
            nums = re.findall(r'col_type4[^>]*>\s*(\d+)\s*</td>', row)
            rise = int(nums[0]) if len(nums) > 0 else 0
            flat = int(nums[1]) if len(nums) > 1 else 0
            fall = int(nums[2]) if len(nums) > 2 else 0
            out.append({
                "no": nm.group(1),
                "name": nm.group(2).strip(),
                "change": change,
                "rise": rise,
                "flat": flat,
                "fall": fall,
            })
            found += 1
        if found == 0:
            break
        time.sleep(0.15)
    out.sort(key=lambda t: t["change"], reverse=True)
    print(f"  Naver themes: {len(out)}")
    return out


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

    naver_themes = fetch_naver_themes()
    enrich_top_themes_with_stocks(naver_themes, top_n=10)

    naver_industries = fetch_naver_industries()
    enrich_top_industries_with_stocks(naver_industries, top_n=10)

    update_descriptions(naver_themes, naver_industries)

    day_str = datetime.now(KST).strftime("%Y%m%d")
    save_history(naver_themes, day_str)

    out = {
        "updated": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        "trading_day": datetime.now(KST).strftime("%Y%m%d"),
        "indices": indices,
        "stocks": stocks,
        "new_highs": new_highs,
        "naver_themes": naver_themes,
        "naver_industries": naver_industries,
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
