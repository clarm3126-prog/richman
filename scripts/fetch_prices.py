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

sys.path.insert(0, str(Path(__file__).parent))
from common import load_json, send_message
from screener import is_excluded_security

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


# ================================
# 네이버 모바일 JSON API
# ================================
#
# 2026년 9월, 네이버가 finance.naver.com의 HTML 시세표를 stock.naver.com
# SPA로 옮겼다. 옛 주소는 301로 넘어가고 받아지는 문서에 <table>이 아예
# 없다. 정규식이든 BeautifulSoup이든 파싱할 것이 남아 있지 않다.
#
# 같은 값을 모바일 JSON API가 그대로 준다. 화면을 긁는 대신 이쪽을 쓴다.
# 표 구조가 바뀌어도 깨지지 않고, 숫자를 *Raw 필드로 받아 콤마를 풀 일도
# 없다. common.py의 fetch_theme_members()가 이미 같은 API를 쓰고 있었다.
#
# 살아남은 옛 주소가 하나 있다. item/sise_day.naver(일별시세)는 그대로라
# screener.py의 fetch_stock_history()는 건드리지 않았다.

MOBILE_API = "https://m.stock.naver.com/api"
MOBILE_HEADERS = {
    "User-Agent": HEADERS["User-Agent"],
    "Referer": "https://m.stock.naver.com/",
    "Accept": "application/json",
}

# 한 번에 받을 수 있는 최대치. 200을 넣으면 JSON이 아닌 것이 돌아온다.
PAGE_SIZE = 100


def _mapi(path, params=None, timeout=12, retries=2):
    """모바일 API 한 번 호출. 실패하면 None을 돌려준다.

    429는 잠깐 쉬었다 다시 건다. 전 종목을 페이지로 훑으므로 몰아치면
    막힌다. 그 밖의 오류는 호출한 쪽이 빈 값으로 처리하게 둔다.
    """
    url = f"{MOBILE_API}/{path.lstrip('/')}"
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, headers=MOBILE_HEADERS, params=params, timeout=timeout)
            if r.status_code == 429:
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code != 200:
                return None
            return r.json()
        except Exception:
            if attempt == retries:
                return None
            time.sleep(0.5)
    return None


def _api_int(raw, text=None):
    """모바일 API의 정수. *Raw 필드가 있으면 그걸 쓰고, 없으면 콤마를 푼다.

    응답에는 같은 값이 두 벌 들어 있다. closePrice는 "259,500" 같은
    표시용 문자열이고 closePriceRaw는 259500이다. 사람이 읽을 일이
    없으므로 Raw를 먼저 본다.
    """
    if isinstance(raw, (int, float)):
        return int(raw)
    if raw not in (None, ""):
        return parse_int(str(raw))
    return parse_int(text)


def _api_float(value, default=0.0):
    """등락률처럼 부호가 문자열에 들어 있는 값. %와 콤마를 떼고 읽는다."""
    try:
        return float(str(value).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return default


def _fmt_bizdate(value):
    """API의 20260911을 예전 화면 표기인 2026.09.11로 맞춘다.

    이 값이 어디까지 흘러가는지 다 따라가기보다, 바꾸기 전과 같은 모양으로
    내보내는 편이 안전하다.
    """
    s = str(value or "")
    return f"{s[0:4]}.{s[4:6]}.{s[6:8]}" if len(s) == 8 and s.isdigit() else s


def fetch_market(sosok):
    """sosok=0: KOSPI, 1: KOSDAQ. 전 종목 시세를 페이지로 훑는다."""
    market = "KOSPI" if sosok == 0 else "KOSDAQ"
    out = {}
    page = 1
    # KOSPI 25페이지, KOSDAQ 19페이지면 끝난다. 가드는 넉넉히 둔다.
    while page <= 60:
        data = _mapi(f"stocks/marketValue/{market}",
                     {"page": page, "pageSize": PAGE_SIZE})
        rows = (data or {}).get("stocks") or []
        if not rows:
            break
        for s in rows:
            code = str(s.get("itemCode") or "").strip().zfill(6)
            if not code.isdigit():
                continue
            out[code] = {
                "name": (s.get("stockName") or "").strip(),
                "market": market,
                "price": _api_int(s.get("closePriceRaw"), s.get("closePrice")),
                "change": _api_float(s.get("fluctuationsRatio")),
                "volume": _api_int(s.get("accumulatedTradingVolumeRaw"),
                                   s.get("accumulatedTradingVolume")),
            }
        if len(rows) < PAGE_SIZE:
            break
        page += 1
        time.sleep(0.12)

    print(f"  {market}: {len(out)} stocks ({page} pages)")
    return out


def _index_history_legacy(code):
    """구 API. count를 아무리 크게 줘도 110개까지만 돌려준다."""
    url = f"https://api.stock.naver.com/chart/domestic/index/{code}?periodType=dayCandle&count=500"
    headers = {**HEADERS, "Referer": "https://stock.naver.com/"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            return []
        out = []
        for p in r.json().get("priceInfos", []):
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
        print(f"  index_history(legacy) {code} failed: {e}")
        return []


def _index_history_range(code, days):
    """기간을 지정해 받는다. 종목 일봉을 받는 곳과 같은 주소다.
    응답이 JSON이 아니라 Python 리터럴(작은따옴표)이라 literal_eval로 읽는다.
    """
    import ast as _ast
    from datetime import timedelta as _td

    today = datetime.now(KST)
    # days 거래일을 확보하려고 달력 날짜로 1.6배 + 30일 여유
    start = (today - _td(days=int(days * 1.6) + 30)).strftime("%Y%m%d")
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
        r = requests.get(url, params=params, headers=headers, timeout=15)
        if r.status_code != 200 or not r.text.strip():
            return []
        data = _ast.literal_eval(r.text.strip())
        if not data or len(data) < 2:
            return []
        # data[0] = 머리글 ['날짜','시가','고가','저가','종가','거래량','외국인소진율']
        out = []
        for row in data[1:]:
            if len(row) < 5:
                continue
            try:
                out.append({
                    "date": str(row[0]),
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                })
            except (ValueError, TypeError):
                continue
        out.sort(key=lambda x: x["date"])
        return out
    except Exception as e:
        print(f"  index_history(range) {code} failed: {e}")
        return []


def fetch_index_history(code, days=252):
    """네이버 지수 일봉 OHLC. code: KOSPI 또는 KOSDAQ.

    미너비니 RS 등수를 매기려면 지수도 1년치(252거래일)가 있어야 한다.
    구 API는 110개까지만 줘서 RS가 아예 계산되지 않았고, 그 탓에 8개 조건을
    전부 통과하는 종목이 늘 0개였다. 그래서 기간을 지정할 수 있는 쪽을 먼저
    쓰고, 막히면 구 API로 내려간다 (짧아도 없는 것보다는 낫다).
    """
    out = _index_history_range(code, days)
    if len(out) < days:
        legacy = _index_history_legacy(code)
        if len(legacy) > len(out):
            print(f"  index_history {code}: 기간 조회 {len(out)}일 → 구 API {len(legacy)}일 사용")
            out = legacy
    return out[-days:] if len(out) > days else out


def fetch_index(code):
    """code='KOSPI' or 'KOSDAQ'. 지수 현재값과 등락률.

    예전에는 #now_value와 #change_value_and_rate를 읽고, 부호가 글자에
    없을 때 ico_up/ico_down 클래스로 방향을 알아냈다. API는 부호가 붙은
    숫자를 바로 주므로 그 추측이 통째로 없어진다.
    """
    data = _mapi(f"index/{code}/basic")
    if not data:
        print(f"  index {code} fetch failed")
        return None
    value = _api_float(data.get("closePrice"), None)
    if value is None:
        return None
    return {"value": value, "change": _api_float(data.get("fluctuationsRatio"))}


def _fetch_groups(kind, label):
    """업종/테마 목록. 둘의 응답 모양이 같아서 한 함수로 쓴다.

    예전에는 등락률의 부호를 red01/nv01 클래스로 알아내고, 상승·보합·하락
    종목수는 td 순서를 세어 집었다. 표의 열이 하나 늘면 조용히 어긋나던
    자리다. API는 riseCount/steadyCount/fallCount로 이름을 붙여 준다.
    """
    out = []
    page = 1
    while page <= 20:
        data = _mapi(f"stocks/{kind}", {"page": page, "pageSize": PAGE_SIZE})
        groups = (data or {}).get("groups") or []
        if not groups:
            break
        for g in groups:
            out.append({
                "no": str(g.get("no") or ""),
                "name": (g.get("name") or "").strip(),
                "change": _api_float(g.get("changeRate")),
                "total": int(g.get("totalCount") or 0),
                "rise": int(g.get("riseCount") or 0),
                "flat": int(g.get("steadyCount") or 0),
                "fall": int(g.get("fallCount") or 0),
            })
        if len(groups) < PAGE_SIZE:
            break
        page += 1
        time.sleep(0.12)

    out.sort(key=lambda i: i["change"], reverse=True)
    print(f"  Naver {label}: {len(out)}")
    return out


def fetch_naver_industries():
    """업종 목록 (등락률·상승/하락 종목수 포함)."""
    return _fetch_groups("industry", "industries")


def _enrich_with_stocks(items, kind, top_n=10):
    """공통: 상위 N개 항목에 멤버 종목 리스트 추가. kind in {'theme','industry'}"""
    base = "industry" if kind == "industry" else "theme"
    headers = {**HEADERS, "Referer": "https://m.stock.naver.com/"}
    for item in items[:top_n]:
        no = item.get("no")
        if not no:
            continue
        url = f"https://m.stock.naver.com/api/stocks/{base}/{no}?page=1&pageSize=50"
        try:
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code != 200:
                item["stocks"] = []
                continue
            data = r.json()
            item["stocks"] = [
                {"code": str(s.get("itemCode")).zfill(6), "name": s.get("stockName")}
                for s in data.get("stocks", [])
                if s.get("itemCode") and s.get("stockName")
            ]
        except Exception as e:
            print(f"  {kind} {no} stocks fetch failed: {e}")
            item["stocks"] = []
        time.sleep(0.1)
    print(f"  enriched {top_n} {kind}s with member stocks")


def enrich_top_industries_with_stocks(industries, top_n=10):
    _enrich_with_stocks(industries, "industry", top_n)


def enrich_top_themes_with_stocks(themes, top_n=10):
    _enrich_with_stocks(themes, "theme", top_n)


def send_telegram(bot_token, chat_id, text):
    """Telegram 봇으로 메시지 전송. 성공/실패 + 응답 메시지 반환."""
    return send_message(bot_token, chat_id, text, parse_mode="Markdown", timeout=10)


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
    triggered = set(load_json(triggered_path, []))

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
    descriptions = load_json(desc_path, {})

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


def save_history(themes, industries, trading_day):
    """일별 테마/업종 강도 데이터를 data/history/{YYYYMMDD}.json 으로 저장."""
    if not trading_day or (not themes and not industries):
        return
    hist_dir = Path("data/history")
    hist_dir.mkdir(parents=True, exist_ok=True)
    # 멤버 stocks 필드 제외 (히스토리 파일 크기 절약)
    def lite(items):
        return [{k: v for k, v in it.items() if k != "stocks"} for it in (items or [])]
    today_file = hist_dir / f"{trading_day}.json"
    with open(today_file, "w", encoding="utf-8") as f:
        json.dump({
            "updated": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
            "trading_day": trading_day,
            "themes": lite(themes),
            "industries": lite(industries),
        }, f, ensure_ascii=False, separators=(",", ":"))
    dates = sorted(
        [f.stem for f in hist_dir.glob("*.json") if f.stem != "index"],
        reverse=True,
    )
    with open(hist_dir / "index.json", "w", encoding="utf-8") as f:
        json.dump({"dates": dates}, f)
    print(f"  history saved: {today_file.name} (themes={len(themes or [])}, industries={len(industries or [])})")


def update_volume_data(stocks):
    """전일 대비 거래량 급증 종목 계산 + 다음날 비교용 데이터 저장."""
    prev_path = Path("data/prev_day_volumes.json")
    today_str = datetime.now(KST).strftime("%Y%m%d")
    surges = []
    if prev_path.exists():
        try:
            prev_data = load_json(prev_path, {})
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
                surges = surges[:60]  # KOSPI/KOSDAQ 필터 적용 후도 충분한 buffer
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


def _fetch_stock_frgn(code):
    """종목별 최신 외국인/기관 순매매 주식수.

    예전에는 frgn.naver의 두 번째 표에서 td를 세어 5번째를 기관, 6번째를
    외국인으로 읽었다. 열 순서에 기대는 코드라 표가 바뀌면 값이 서로
    바뀌어도 알 길이 없었다. API는 이름이 붙어 있다.
    """
    rows = _mapi(f"stock/{code}/trend", {"pageSize": 2}, timeout=8, retries=1)
    if not isinstance(rows, list) or not rows:
        return code, None
    row = rows[0]
    inst = row.get("organPureBuyQuant")
    foreign = row.get("foreignerPureBuyQuant")
    if inst is None or foreign is None:
        return code, None
    return code, {
        "institution_net": _api_int(None, inst),
        "foreign_net": _api_int(None, foreign),
        "date": _fmt_bizdate(row.get("bizdate")),
    }


def compute_investor_rankings(stocks, top_n_traded=80, top_n_per_list=15):
    """거래대금 상위 종목 frgn 데이터 수집 → 외국인/기관 순매수/순매도 ranking."""
    import concurrent.futures
    top_kospi, top_kosdaq = [], []
    for code, s in stocks.items():
        if s.get("volume", 0) < 1000 or s.get("price", 0) < 100:
            continue
        tv = s["price"] * s["volume"]
        if s.get("market") == "KOSPI":
            top_kospi.append((code, tv))
        elif s.get("market") == "KOSDAQ":
            top_kosdaq.append((code, tv))
    top_kospi.sort(key=lambda x: x[1], reverse=True)
    top_kosdaq.sort(key=lambda x: x[1], reverse=True)
    candidate_codes = (
        [c for c, _ in top_kospi[:top_n_traded]] +
        [c for c, _ in top_kosdaq[:top_n_traded]]
    )
    print(f"  fetching frgn data for {len(candidate_codes)} stocks (top traded)...")

    investor_data = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=15) as ex:
        futures = {ex.submit(_fetch_stock_frgn, c): c for c in candidate_codes}
        for f in concurrent.futures.as_completed(futures):
            code, data = f.result()
            if data:
                investor_data[code] = data
    print(f"  fetched {len(investor_data)} frgn records")

    rankings = {
        "foreign": {"KOSPI": {"buy": [], "sell": []}, "KOSDAQ": {"buy": [], "sell": []}},
        "institution": {"KOSPI": {"buy": [], "sell": []}, "KOSDAQ": {"buy": [], "sell": []}},
    }
    for code, inv in investor_data.items():
        s = stocks.get(code)
        if not s:
            continue
        market = s.get("market")
        if market not in ("KOSPI", "KOSDAQ"):
            continue
        base = {
            "code": code, "name": s["name"], "market": market,
            "price": s["price"], "change": s["change"], "volume": s["volume"],
        }
        f_shares = inv["foreign_net"]
        f_amount = f_shares * s["price"]
        if f_amount != 0:
            target = "buy" if f_amount > 0 else "sell"
            rankings["foreign"][market][target].append({
                **base, "shares": f_shares, "amount": f_amount,
            })
        i_shares = inv["institution_net"]
        i_amount = i_shares * s["price"]
        if i_amount != 0:
            target = "buy" if i_amount > 0 else "sell"
            rankings["institution"][market][target].append({
                **base, "shares": i_shares, "amount": i_amount,
            })

    for inv_type in rankings:
        for market in rankings[inv_type]:
            buy_list = rankings[inv_type][market]["buy"]
            buy_list.sort(key=lambda x: x["amount"], reverse=True)
            rankings[inv_type][market]["buy"] = buy_list[:top_n_per_list]
            sell_list = rankings[inv_type][market]["sell"]
            sell_list.sort(key=lambda x: x["amount"])
            rankings[inv_type][market]["sell"] = sell_list[:top_n_per_list]
    return rankings


def fetch_watchlist_stock_history(days=7):
    """관심 종목 일봉 종가 (sparkline용)."""
    wl_path = Path("data/watchlist.json")
    if not wl_path.exists():
        return {}
    wl = load_json(wl_path, {})
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
    """테마 목록 (등락률·상승/하락 종목수 포함)."""
    return _fetch_groups("theme", "themes")


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


def detect_ath_breakouts(stocks, investor_top, bot_token, chat_id):
    """역사적 신고가 + 거래량 동반 돌파 감지 → Telegram (A 장중 / B 마감).
    data/ath_cache.json (주간 갱신)의 ATH/평균거래량 활용.
    A (장중): 9~16시 — 거래량 1.0배+ / B (마감): 16시+ — 거래량 1.5배+
    """
    ath_path = Path("data/ath_cache.json")
    if not ath_path.exists():
        print("  ath_cache.json 없음 — ATH 돌파 감지 skip (fetch_ath 먼저 실행 필요)")
        return
    ath_cache = load_json(ath_path, {}).get("stocks", {})
    if not ath_cache:
        return

    now = datetime.now(KST)
    today_str = now.strftime("%Y%m%d")
    today_date = now.strftime("%Y-%m-%d")
    is_close = now.hour >= 16

    # 수급: 외국인/기관 순매수 종목 set
    net_buyers = set()
    if investor_top:
        for inv_type in ("foreign", "institution"):
            d = investor_top.get(inv_type, {}) or {}
            for mkt in ("KOSPI", "KOSDAQ"):
                for s in (d.get(mkt, {}) or {}).get("buy", []) or []:
                    if s.get("code"):
                        net_buyers.add(s["code"])

    # ATH 돌파 후보 탐색
    # ATH_MIN_BASE_DAYS: 역대 고점이 이만큼 오래된 것만 = 진짜 베이스 돌파.
    # 고점이 최근이면 = 이미 계속 신고가 달리는 종목 → 제외 (chasing 방지)
    ATH_MIN_BASE_DAYS = 60
    breakouts = []
    skipped_runner = 0
    vol_threshold = 1.5 if is_close else 1.0
    today_d = now.date()
    for code, s in stocks.items():
        info = ath_cache.get(code)
        if not info:
            continue
        # ETF/ETN/선물/합성/SPAC/우선주 등 제외 (캐시가 오래돼도 실시간 필터)
        excl, _ = is_excluded_security(s.get("name", ""), code)
        if excl:
            continue
        ath = info.get("ath", 0)
        avg_vol = info.get("avg_vol_20d", 0)
        price = s.get("price", 0)
        vol = s.get("volume", 0)
        if ath <= 0 or price <= 0:
            continue
        # 1. 역사적 신고가 돌파 (현재가 >= 캐시된 ATH)
        if price < ath:
            continue
        # 2. 베이스 신선도 — 역대 고점이 60일+ 오래된 것만 (이미 달리는 종목 제외)
        ath_date_str = info.get("ath_date", "")
        if not ath_date_str:
            continue
        try:
            ath_dt = datetime.strptime(ath_date_str, "%Y%m%d").date()
            base_days = (today_d - ath_dt).days
        except Exception:
            continue
        if base_days < ATH_MIN_BASE_DAYS:
            skipped_runner += 1
            continue
        # 3. 거래량 동반
        vol_ratio = vol / avg_vol if avg_vol > 0 else 0
        if vol_ratio < vol_threshold:
            continue
        breakouts.append({
            "code": code,
            "name": s.get("name", code),
            "market": s.get("market", ""),
            "price": price,
            "change": s.get("change", 0),
            "ath": ath,
            "ath_date": ath_date_str,
            "base_days": base_days,
            "volume": vol,
            "vol_ratio": round(vol_ratio, 2),
            "supply_demand": code in net_buyers,
        })
    breakouts.sort(key=lambda x: x["vol_ratio"], reverse=True)
    print(f"  ATH breakouts: {len(breakouts)} ({'마감' if is_close else '장중'}, vol≥{vol_threshold}x) · 이미 달리는 종목 {skipped_runner}개 제외")

    # ath_breakouts.json 저장 (frontend 탭용)
    bo_path = Path("data/ath_breakouts.json")
    existing = {"trading_day": today_str, "intraday": [], "close": []}
    if bo_path.exists():
        try:
            existing = load_json(bo_path, {})
            if existing.get("trading_day") != today_str:
                existing = {"trading_day": today_str, "intraday": [], "close": []}
        except Exception:
            existing = {"trading_day": today_str, "intraday": [], "close": []}
    if is_close:
        existing["close"] = breakouts
    else:
        # intraday: 누적 (code 기준 merge — 장중 한번 뜬 건 계속 표시)
        merged = {b["code"]: b for b in existing.get("intraday", [])}
        for b in breakouts:
            merged[b["code"]] = b
        existing["intraday"] = sorted(merged.values(), key=lambda x: x["vol_ratio"], reverse=True)
    existing["trading_day"] = today_str
    existing["updated"] = now.strftime("%Y-%m-%d %H:%M:%S KST")
    bo_path.write_text(json.dumps(existing, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # Telegram 알림 (신규만, 7일 dedup)
    if not bot_token or not chat_id:
        return
    alerted_path = Path("data/ath_breakout_alerted.json")
    alerted = {"intraday": {}, "close": {}}
    if alerted_path.exists():
        try:
            alerted = load_json(alerted_path, {})
            alerted.setdefault("intraday", {})
            alerted.setdefault("close", {})
        except Exception:
            pass
    cutoff = now.timestamp() - 7 * 86400
    cat = "close" if is_close else "intraday"
    cat_alerted = alerted[cat]
    for c in list(cat_alerted.keys()):
        try:
            if datetime.strptime(cat_alerted[c], "%Y-%m-%d").timestamp() < cutoff:
                del cat_alerted[c]
        except Exception:
            pass
    new_alerts = [b for b in breakouts if b["code"] not in cat_alerted]
    if not new_alerts:
        return
    header = "🚀 *역사적 신고가 돌파* (마감 확정)" if is_close else "🚀 *역사적 신고가 돌파* (장중)"
    lines = [f"{header} — {today_date}\n"]
    for b in new_alerts[:12]:
        sd = " · ⭐수급 동반" if b["supply_demand"] else ""
        sign = "+" if b["change"] > 0 else ""
        base_d = b.get("base_days", 0)
        base_txt = f"{base_d // 30}개월" if base_d >= 30 else f"{base_d}일"
        lines.append(
            f"• *{b['name']}* (`{b['code']}` {b['market']})\n"
            f"  {b['price']:,}원 ({sign}{b['change']:.2f}%) · 거래량 {b['vol_ratio']}x{sd}\n"
            f"  📈 {base_txt}만의 역사적 신고가 돌파 (이전 ATH {b['ath']:,.0f}원)"
        )
    if len(new_alerts) > 12:
        lines.append(f"... 외 {len(new_alerts) - 12}개")
    msg = "\n".join(lines)
    ok, info_msg = send_telegram(bot_token, chat_id, msg)
    if ok:
        for b in new_alerts:
            cat_alerted[b["code"]] = today_date
        alerted_path.write_text(json.dumps(alerted, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  ✅ ATH breakout telegram: {len(new_alerts)} new ({cat})")
    else:
        print(f"  ❌ ATH telegram failed: {info_msg}")


def find_new_highs(stocks):
    """52주 신고가 종목 찾기. 캐시된 52w high 활용 + 후보 필터로 호출 절감.
    캐시 구조: data/52w_cache.json = {date: 'YYYYMMDD', high: {code: int}}
    하루 1번만 전체 갱신, 그 외엔 캐시 사용 + 가격이 캐시 ≈90% 이상인 종목만 재확인.
    """
    import concurrent.futures
    cache_path = Path("data/52w_cache.json")
    today_str = datetime.now(KST).strftime("%Y%m%d")
    cache = load_json(cache_path, {"date": "", "high": {}})

    is_full_refresh = cache.get("date") != today_str
    cached_high = cache.get("high", {})

    if is_full_refresh:
        # 하루 첫 실행: 전체 종목 대상
        candidates = [
            code for code, s in stocks.items()
            if s.get("volume", 0) > 5000 and s.get("price", 0) > 0
        ]
        print(f"  [FULL] checking {len(candidates)} candidates for 52w high (1회/일)...")
    else:
        # 캐시 있음: 오늘 가격이 캐시값의 90% 이상인 종목만 재확인 (가능성 있는 것만)
        candidates = []
        for code, s in stocks.items():
            if s.get("volume", 0) <= 5000 or s.get("price", 0) <= 0:
                continue
            ch = cached_high.get(code, 0)
            if ch == 0 or s["price"] >= ch * 0.90:  # 90% 임계 (comment와 일치)
                candidates.append(code)
        print(f"  [INCREMENTAL] checking {len(candidates)} stocks near 52w high (rest cached)...")

    # 캐시 OVERWRITE 전 snapshot — fallback 비교용
    cached_high_snapshot = dict(cached_high)
    candidates_set = set(candidates)

    new_highs = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=15) as ex:
        futures = {ex.submit(fetch_52w_high_for_stock, c): c for c in candidates}
        done = 0
        for f in concurrent.futures.as_completed(futures):
            code, high_52w, today_high = f.result()
            done += 1
            if done % 500 == 0:
                print(f"    ...{done}/{len(candidates)} checked")
            if high_52w:
                cached_high[code] = high_52w
            if high_52w and today_high and today_high >= high_52w:
                new_highs.append(code)

    # incremental 모드: 90% 임계 미달이라 fetch 안 한 종목 중에도
    # 오늘 가격이 (snapshot으로 본 어제 기준의) 52w high를 돌파한 게 있을 수 있음.
    # 단, candidates에 포함된 종목은 이미 정확히 평가됐으므로 제외.
    if not is_full_refresh:
        for code, ch in cached_high_snapshot.items():
            if code in candidates_set or code in new_highs:
                continue
            s = stocks.get(code)
            if not s or s.get("price", 0) < ch:
                continue
            new_highs.append(code)

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps({"date": today_str, "high": cached_high}, ensure_ascii=False),
        encoding="utf-8",
    )
    new_highs.sort(key=lambda c: stocks[c].get("change", 0) if c in stocks else 0, reverse=True)
    print(f"  Found {len(new_highs)} stocks at 52w high (cache: {len(cached_high)})")
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
    # 252일 = 1년 거래일. 미너비니 RS 등수 계산에 필요한 최소치다.
    kospi_hist = fetch_index_history("KOSPI", 252)
    kosdaq_hist = fetch_index_history("KOSDAQ", 252)
    if indices.get("kospi") and kospi_hist:
        indices["kospi"]["history"] = kospi_hist
    if indices.get("kosdaq") and kosdaq_hist:
        indices["kosdaq"]["history"] = kosdaq_hist
    print(f"  index history: KOSPI={len(kospi_hist)}, KOSDAQ={len(kosdaq_hist)}")

    print("Checking price alerts...")
    check_alerts_and_notify(stocks)

    print("Computing volume surges...")
    volume_surges = update_volume_data(stocks)

    # 거래량 surge daily archive (장 마감 후만)
    if datetime.now(KST).hour >= 16 and volume_surges:
        archive_dir = Path("data/volume_history")
        archive_dir.mkdir(parents=True, exist_ok=True)
        today_str = datetime.now(KST).strftime("%Y%m%d")
        archive_path = archive_dir / f"{today_str}.json"
        # 가벼운 버전: top 30
        lite = [{
            "code": s["code"], "name": s["name"], "market": s.get("market", ""),
            "price": s["price"], "change": s["change"], "ratio": s["ratio"],
            "volume": s["volume"],
        } for s in volume_surges[:30]]
        archive_path.write_text(json.dumps({
            "trading_day": today_str,
            "stocks": lite,
        }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(f"  archived volume surges: {archive_path.name} ({len(lite)} stocks)")

    print("Computing investor rankings (real net buy/sell amounts)...")
    investor_top = compute_investor_rankings(stocks, top_n_traded=80, top_n_per_list=15)

    print("Detecting ATH breakouts (역사적 신고가 + 거래량)...")
    _ath_bot = os.environ.get("TELEGRAM_BOT_TOKEN")
    _ath_chat = os.environ.get("TELEGRAM_CHAT_ID")
    detect_ath_breakouts(stocks, investor_top, _ath_bot, _ath_chat)

    print("Fetching watchlist stock history...")
    watchlist_history = fetch_watchlist_stock_history(7)

    new_highs = find_new_highs(stocks)

    naver_themes = fetch_naver_themes()
    enrich_top_themes_with_stocks(naver_themes, top_n=10)

    naver_industries = fetch_naver_industries()
    enrich_top_industries_with_stocks(naver_industries, top_n=10)

    update_descriptions(naver_themes, naver_industries)

    day_str = datetime.now(KST).strftime("%Y%m%d")
    save_history(naver_themes, naver_industries, day_str)

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
