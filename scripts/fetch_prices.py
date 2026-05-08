#!/usr/bin/env python3
"""네이버 금융에서 KOSPI/KOSDAQ 전 종목 시세를 수집해 data/market.json으로 저장."""
import json
import os
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


def fetch_index_history(code, count=60):
    """네이버 지수 일봉 OHLC. code: KOSPI 또는 KOSDAQ"""
    url = f"https://api.stock.naver.com/chart/domestic/index/{code}?periodType=dayCandle&count={count}"
    headers = {**HEADERS, "Referer": "https://stock.naver.com/"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            return []
        data = r.json()
        out = []
        for p in data.get("priceInfos", []):
            d = p.get("localDate")
            if not d:
                continue
            try:
                out.append({
                    "date": str(d),
                    "open": float(p.get("openPrice", 0)),
                    "high": float(p.get("highPrice", 0)),
                    "low": float(p.get("lowPrice", 0)),
                    "close": float(p.get("closePrice", 0)),
                })
            except (ValueError, TypeError):
                continue
        out.sort(key=lambda x: x["date"])
        return out
    except Exception as e:
        print(f"  index_history {code} failed: {e}")
        return []


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


def send_telegram(bot_token, chat_id, text):
    """Telegram 봇으로 메시지 전송. 성공/실패 + 응답 메시지 반환."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        r = requests.post(
            url,
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown", "disable_web_page_preview": True},
            timeout=10,
        )
        if r.status_code == 200:
            return True, "OK"
        # 실패 시 Telegram 에러 메시지 반환
        try:
            err = r.json().get("description", r.text[:200])
        except Exception:
            err = r.text[:200]
        return False, f"HTTP {r.status_code}: {err}"
    except Exception as e:
        return False, f"Exception: {e}"


def check_alerts_and_notify(stocks):
    """data/alerts_config.json 읽고 도달한 알림에 대해 Telegram 발송."""
    config_path = Path("data/alerts_config.json")
    if not config_path.exists():
        print("  no alerts_config.json (skip alerts)")
        return
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        print("  TELEGRAM_BOT_TOKEN/CHAT_ID env not set (skip alerts)")
        return
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  alerts_config parse failed: {e}")
        return
    alerts = config.get("alerts", [])
    if not alerts:
        print("  alerts_config has 0 alerts")
        return

    triggered_path = Path("data/triggered_alerts.json")
    triggered = set()
    if triggered_path.exists():
        try:
            triggered = set(json.loads(triggered_path.read_text(encoding="utf-8")))
        except Exception:
            triggered = set()

    new_count = 0
    for alert in alerts:
        code = str(alert.get("code", "")).zfill(6)
        target = alert.get("target")
        direction = alert.get("direction", "above")
        if not code or not target:
            continue
        alert_key = f"{code}_{target}_{direction}"
        if alert_key in triggered:
            continue
        stock = stocks.get(code)
        if not stock:
            continue
        price = stock.get("price", 0)
        is_hit = (
            (direction == "above" and price >= target) or
            (direction == "below" and price <= target)
        )
        if not is_hit:
            continue
        sign = "+" if stock["change"] > 0 else ""
        dir_text = "↑ 돌파" if direction == "above" else "↓ 하락"
        msg = (
            f"🚨 *{stock['name']}* 알림 도달\n\n"
            f"목표: `{target:,}원` {dir_text}\n"
            f"현재: *{price:,}원* ({sign}{stock['change']:.2f}%)\n"
            f"거래량: {stock.get('volume', 0):,}\n\n"
            f"종목코드: `{code}` ({stock.get('market', '')})"
        )
        ok, info = send_telegram(bot_token, chat_id, msg)
        if ok:
            triggered.add(alert_key)
            new_count += 1
            print(f"  alert ✓ SENT: {stock['name']} {target:,}원 {direction}")
        else:
            print(f"  alert ✗ FAILED: {stock['name']} {target:,}원 {direction} | {info}")

    if new_count > 0:
        triggered_path.write_text(
            json.dumps(sorted(triggered), ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"  saved {len(triggered)} triggered alerts ({new_count} new)")
    else:
        print(f"  no new alerts triggered (already sent: {len(triggered)})")


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


def update_volume_data(stocks):
    """전일 대비 거래량 급증 종목 계산 + 다음날 비교용 데이터 저장."""
    prev_path = Path("data/prev_day_volumes.json")
    today_str = datetime.now(KST).strftime("%Y%m%d")
    surges = []
    if prev_path.exists():
        try:
            prev_data = json.loads(prev_path.read_text(encoding="utf-8"))
            prev_date = prev_data.get("date")
            prev_volumes = prev_data.get("volumes", {})
            if prev_date and prev_date < today_str:
                # 어제 데이터로 오늘 surge 계산
                for code, s in stocks.items():
                    pv = prev_volumes.get(code, 0)
                    if pv < 10000:
                        continue
                    cv = s.get("volume", 0)
                    if cv < pv * 1.5:
                        continue
                    surges.append({
                        "code": code,
                        "name": s["name"],
                        "market": s.get("market", ""),
                        "price": s["price"],
                        "change": s["change"],
                        "volume": cv,
                        "prev_volume": pv,
                        "ratio": round(cv / pv, 2),
                    })
                surges.sort(key=lambda x: x["ratio"], reverse=True)
                surges = surges[:30]
        except Exception as e:
            print(f"  vol prev read err: {e}")

    # 장 마감 이후 (KST 16시 이후)에만 다음날용 스냅샷 저장
    now = datetime.now(KST)
    if now.hour >= 16:
        snapshot = {c: s.get("volume", 0) for c, s in stocks.items() if s.get("volume", 0) > 0}
        prev_path.write_text(
            json.dumps({"date": today_str, "volumes": snapshot}, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"  saved {len(snapshot)} prev day volumes")
    print(f"  volume surges: {len(surges)}")
    return surges


def fetch_investor_top():
    """외국인/기관 매매 상위 (KOSPI + KOSDAQ)."""
    out = {"foreign": {"KOSPI": [], "KOSDAQ": []}, "institution": {"KOSPI": [], "KOSDAQ": []}}
    for inv_name, inv_no in [("foreign", "9000"), ("institution", "1000")]:
        for market_name, sosok in [("KOSPI", "01"), ("KOSDAQ", "02")]:
            url = f"https://finance.naver.com/sise/sise_deal_rank.naver?sosok={sosok}&investor_gubun={inv_no}"
            try:
                r = requests.get(url, headers=HEADERS, timeout=15)
                r.encoding = "euc-kr"
                tables = re.findall(r'<table[^>]*class="type_r1"[^>]*>(.*?)</table>', r.text, re.S)
                stocks = []
                # 두번째 테이블이 보통 순매수 상위
                target_table = tables[1] if len(tables) >= 2 else (tables[0] if tables else "")
                rows = re.findall(r'<tr[^>]*>(.*?)</tr>', target_table, re.S)
                for row in rows:
                    nm = re.search(r'item/main\.naver\?code=(\d+)[^>]*class="company"[^>]*>([^<]+)</a>', row)
                    if not nm:
                        continue
                    code = nm.group(1).zfill(6)
                    name = nm.group(2).strip()
                    # 가격 추출
                    price_m = re.search(r'<td class="number">([\d,]+)</td>', row)
                    price = int(price_m.group(1).replace(",", "")) if price_m else 0
                    stocks.append({"code": code, "name": name, "price": price, "market": market_name})
                    if len(stocks) >= 10:
                        break
                out[inv_name][market_name] = stocks
            except Exception as e:
                print(f"  investor {inv_name} {market_name} failed: {e}")
            time.sleep(0.1)
    print(f"  investor top: F-K={len(out['foreign']['KOSPI'])}, F-Q={len(out['foreign']['KOSDAQ'])}, I-K={len(out['institution']['KOSPI'])}, I-Q={len(out['institution']['KOSDAQ'])}")
    return out


def fetch_watchlist_stock_history(days=7):
    """관심 종목 일봉 종가 (sparkline용)."""
    wl_path = Path("data/watchlist.json")
    if not wl_path.exists():
        return {}
    try:
        wl = json.loads(wl_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    codes = [item.get("code") for item in wl.get("watchlist", []) if item.get("code")]
    if not codes:
        return {}
    headers = {**HEADERS, "Referer": "https://stock.naver.com/"}
    histories = {}
    for code in codes[:50]:  # 최대 50개
        url = f"https://api.stock.naver.com/chart/domestic/item/{code}?periodType=dayCandle&count={days}"
        try:
            r = requests.get(url, headers=headers, timeout=8)
            if r.status_code != 200:
                continue
            data = r.json()
            prices = []
            for p in data.get("priceInfos", []):
                cp = p.get("closePrice")
                if cp is not None:
                    try:
                        prices.append(float(cp))
                    except (ValueError, TypeError):
                        pass
            if len(prices) >= 2:
                histories[code] = prices
        except Exception:
            pass
        time.sleep(0.05)
    print(f"  watchlist history: {len(histories)}/{len(codes)}")
    return histories


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
    kospi_hist = fetch_index_history("KOSPI", 60)
    kosdaq_hist = fetch_index_history("KOSDAQ", 60)
    if indices.get("kospi") and kospi_hist:
        indices["kospi"]["history"] = kospi_hist
    if indices.get("kosdaq") and kosdaq_hist:
        indices["kosdaq"]["history"] = kosdaq_hist
    print(f"  index history: KOSPI={len(kospi_hist)}, KOSDAQ={len(kosdaq_hist)}")

    print("Checking price alerts...")
    check_alerts_and_notify(stocks)

    print("Computing volume surges...")
    volume_surges = update_volume_data(stocks)

    print("Fetching investor top...")
    investor_top = fetch_investor_top()

    print("Fetching watchlist stock history...")
    watchlist_history = fetch_watchlist_stock_history(7)

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
        "volume_surges": volume_surges,
        "investor_top": investor_top,
        "watchlist_history": watchlist_history,
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
