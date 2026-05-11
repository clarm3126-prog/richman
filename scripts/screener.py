#!/usr/bin/env python3
"""미너비니 SEPA + Trend Template 스크리너.

3계층:
1. Trend Template (8 기술 조건): MA, 52주 고점/저점, RS Rating
2. SEPA Fundamentals (DART): EPS 성장률, 매출 성장률, 영업이익률
3. Setup/Liquidity: 거래대금, 5일 tightness

출력: data/screener_results.json (통과 종목 + 점수 + 상세)
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
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; MinerviniScreener/1.0)"}

DART_KEY = os.environ.get("DART_API_KEY", "")
DART_BASE = "https://opendart.fss.or.kr/api"

# ================================
# DART (재무 데이터) 모듈
# ================================

def fetch_dart_corp_codes():
    """DART의 모든 회사 corp_code 매핑 (stock_code → corp_code). 1회성."""
    if not DART_KEY:
        raise RuntimeError("DART_API_KEY env not set")
    cache_path = Path("data/dart_corp_codes.json")
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    print("  fetching DART corp codes (one-time, ~3MB zip)...")
    url = f"{DART_BASE}/corpCode.xml?crtfc_key={DART_KEY}"
    r = requests.get(url, timeout=30)
    z = zipfile.ZipFile(io.BytesIO(r.content))
    xml_bytes = z.read("CORPCODE.xml")
    root = ET.fromstring(xml_bytes)
    mapping = {}
    for item in root.findall("list"):
        corp = item.find("corp_code")
        stock = item.find("stock_code")
        if corp is None or stock is None:
            continue
        s = (stock.text or "").strip()
        if s and s.isdigit():
            mapping[s.zfill(6)] = corp.text.strip()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")
    print(f"  saved {len(mapping)} corp_code mappings")
    return mapping


def fetch_financial(corp_code, year, reprt_code):
    """단일 회사의 분기/연간 재무제표 조회.
    reprt_code: 11013=1Q, 11012=반기(2Q), 11014=3Q, 11011=사업(연간)
    """
    url = f"{DART_BASE}/fnlttSinglAcntAll.json"
    params = {
        "crtfc_key": DART_KEY,
        "corp_code": corp_code,
        "bsns_year": str(year),
        "reprt_code": reprt_code,
        "fs_div": "CFS",  # 연결재무제표
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
        if data.get("status") == "000":
            return data.get("list", [])
        # 연결 없으면 별도 시도
        if data.get("status") == "013":
            params["fs_div"] = "OFS"
            r = requests.get(url, params=params, timeout=10)
            data = r.json()
            if data.get("status") == "000":
                return data.get("list", [])
        return None
    except Exception:
        return None


def parse_financials(items):
    """DART 재무제표 list에서 핵심 지표 추출."""
    metrics = {"매출액": 0, "영업이익": 0, "당기순이익": 0, "EPS": 0}
    if not items:
        return metrics
    for item in items:
        name = item.get("account_nm", "")
        amount_str = item.get("thstrm_amount", "0").replace(",", "").replace(" ", "")
        try:
            value = int(amount_str) if amount_str and amount_str not in ("-", "") else 0
        except (ValueError, TypeError):
            value = 0
        if name in ("매출액", "수익(매출액)"):
            metrics["매출액"] = value
        elif name == "영업이익":
            metrics["영업이익"] = value
        elif name == "당기순이익":
            metrics["당기순이익"] = value
        elif "기본주당" in name and "이익" in name:
            metrics["EPS"] = value
    if metrics["매출액"] > 0:
        metrics["영업이익률"] = round(metrics["영업이익"] / metrics["매출액"] * 100, 2)
    else:
        metrics["영업이익률"] = 0
    return metrics


def fetch_all_quarterly_data(corp_codes_map, target_codes, max_workers=8):
    """대상 종목들의 최근 5분기 + 3년 연간 재무 데이터 수집.
    캐시: data/dart_financials.json
    """
    cache_path = Path("data/dart_financials.json")
    cache = {}
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            cache = {}

    today = datetime.now(KST)
    cur_year = today.year
    # 최근 5분기 + 직전 5분기 (YoY 비교용)
    quarters = []
    for y in range(cur_year, cur_year - 3, -1):
        for q_code, q_name in [("11014", "3Q"), ("11012", "2Q"), ("11013", "1Q"), ("11011", "Y")]:
            quarters.append((y, q_code, q_name))
    quarters = quarters[:14]  # 최근 14개 정도

    needed_codes = [c for c in target_codes if c in corp_codes_map]
    print(f"  {len(needed_codes)} stocks have corp_code mapping")

    def fetch_one(stock_code):
        corp_code = corp_codes_map.get(stock_code)
        if not corp_code:
            return stock_code, None
        existing = cache.get(stock_code, {})
        last_updated = existing.get("_updated_date", "")
        # 1주일 내 업데이트면 skip
        today_str = today.strftime("%Y%m%d")
        if last_updated == today_str:
            return stock_code, existing
        result = {"_updated_date": today_str, "quarters": {}, "annuals": {}}
        for year, rcode, qname in quarters:
            key = f"{year}_{qname}"
            # 캐시에 이미 있고 과거 데이터면 재사용
            if existing.get("quarters", {}).get(key) and year < cur_year - 1:
                result["quarters"][key] = existing["quarters"][key]
                continue
            items = fetch_financial(corp_code, year, rcode)
            if items is None:
                continue
            metrics = parse_financials(items)
            if metrics["매출액"] > 0:
                result["quarters"][key] = metrics
            time.sleep(0.05)  # rate limit
        return stock_code, result

    print(f"  fetching financials for {len(needed_codes)} stocks (병렬 {max_workers})...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(fetch_one, c): c for c in needed_codes}
        done = 0
        for f in concurrent.futures.as_completed(futures):
            code, data = f.result()
            done += 1
            if done % 50 == 0:
                print(f"    ...{done}/{len(needed_codes)} done")
            if data:
                cache[code] = data

    cache_path.write_text(json.dumps(cache, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"  saved financial data: {len(cache)} stocks total")
    return cache


# ================================
# 가격 히스토리 (Naver, 252일)
# ================================

def fetch_stock_history(code, days=252):
    """종목 일봉 OHLC."""
    url = f"https://api.stock.naver.com/chart/domestic/item/{code}?periodType=dayCandle&count={days}"
    headers = {**HEADERS, "Referer": "https://stock.naver.com/"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            return code, []
        data = r.json()
        out = []
        for p in data.get("priceInfos", []):
            d = p.get("localDate")
            close = p.get("closePrice")
            high = p.get("highPrice")
            low = p.get("lowPrice")
            openp = p.get("openPrice")
            vol = p.get("accumulatedTradingVolume")
            if d and close is not None:
                try:
                    out.append({
                        "date": str(d),
                        "open": float(openp) if openp else 0,
                        "high": float(high) if high else 0,
                        "low": float(low) if low else 0,
                        "close": float(close),
                        "volume": int(vol) if vol else 0,
                    })
                except (ValueError, TypeError):
                    pass
        out.sort(key=lambda x: x["date"])
        return code, out
    except Exception:
        return code, []


def fetch_all_stock_history(codes, days=252, max_workers=12):
    """모든 대상 종목의 252일 OHLC."""
    print(f"  fetching {days}-day OHLC for {len(codes)} stocks...")
    histories = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(fetch_stock_history, c, days): c for c in codes}
        done = 0
        for f in concurrent.futures.as_completed(futures):
            code, history = f.result()
            done += 1
            if done % 100 == 0:
                print(f"    ...{done}/{len(codes)} done")
            if len(history) >= 30:
                histories[code] = history
    print(f"  got history for {len(histories)} stocks (≥30 days)")
    return histories


# ================================
# 기술적 분석 헬퍼
# ================================

def sma(values, period):
    """단순이동평균 마지막 값."""
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def is_uptrend(values, period, min_days=20):
    """최근 min_days 동안 평균이 상승했는지."""
    if len(values) < period + min_days:
        return False
    recent_ma = sma(values, period)
    past_ma = sum(values[-(period + min_days):-min_days]) / period
    return recent_ma > past_ma


def calc_rs_rating(stock_history, market_history):
    """Relative Strength: 종목 N일 수익률 vs 시장 N일 수익률.
    가중: 3월×40% + 6월×20% + 9월×20% + 12월×20%
    Returns 0~100 percentile-like score (간단한 비율 기반).
    """
    if len(stock_history) < 252 or len(market_history) < 252:
        return None
    weights = [(63, 0.4), (126, 0.2), (189, 0.2), (252, 0.2)]
    score = 0
    for days, w in weights:
        s_ret = (stock_history[-1] / stock_history[-days] - 1) if stock_history[-days] else 0
        m_ret = (market_history[-1] / market_history[-days] - 1) if market_history[-days] else 0
        # 종목이 시장보다 얼마나 잘했나 (비율)
        if m_ret >= 0:
            outperform = s_ret - m_ret
        else:
            outperform = s_ret - m_ret  # both negative comparison
        score += outperform * w
    # Convert to 0-100 scale
    # outperform 0% = 50, outperform +100% = 100, outperform -100% = 0
    return max(0, min(100, 50 + score * 50))


# ================================
# 미너비니 조건 평가
# ================================

def evaluate_minervini(stock_code, history, financials, market_history):
    """단일 종목에 대해 모든 미너비니 조건 평가.
    Returns dict with pass/fail per condition + overall.
    """
    if len(history) < 220:
        return {"eligible": False, "reason": f"history {len(history)} days < 220"}

    closes = [h["close"] for h in history]
    highs = [h["high"] for h in history]
    lows = [h["low"] for h in history]
    opens = [h["open"] for h in history]
    volumes = [h["volume"] for h in history]

    cur_close = closes[-1]
    ma50 = sma(closes, 50)
    ma150 = sma(closes, 150)
    ma200 = sma(closes, 200)

    # === Trend Template (8) ===
    tt = {}
    tt["price_above_ma50"] = cur_close > ma50 if ma50 else False
    tt["price_above_ma150"] = cur_close > ma150 if ma150 else False
    tt["price_above_ma200"] = cur_close > ma200 if ma200 else False
    tt["ma50_above_ma150"] = ma50 > ma150 if (ma50 and ma150) else False
    tt["ma150_above_ma200"] = ma150 > ma200 if (ma150 and ma200) else False
    tt["ma200_uptrend"] = is_uptrend(closes, 200, 21)
    # 52주 고가/저가
    high_52w = max(highs[-min(252, len(highs)):])
    low_52w = min(lows[-min(252, len(lows)):])
    tt["within_25pct_of_52w_high"] = cur_close >= high_52w * 0.75
    tt["above_25pct_from_52w_low"] = cur_close >= low_52w * 1.25
    # RS Rating
    rs = calc_rs_rating(closes, market_history) if market_history else None
    tt["rs_rating_70plus"] = rs is not None and rs >= 70
    tt["_rs_value"] = round(rs, 1) if rs else None

    # === Setup / Liquidity ===
    setup = {}
    if len(history) >= 5:
        last5_high = max(highs[-5:])
        last5_low = min(lows[-5:])
        setup["5day_tightness_10pct"] = (last5_high - last5_low) / last5_low * 100 <= 10
        last5_open = opens[-5]
        last5_close = closes[-1]
        setup["5day_open_close_5pct"] = abs(last5_close - last5_open) / last5_open * 100 <= 5
    else:
        setup["5day_tightness_10pct"] = False
        setup["5day_open_close_5pct"] = False
    # 20일 거래대금 70억 1회 이상
    if len(history) >= 20:
        tv_max = max(history[i]["close"] * history[i]["volume"] for i in range(-20, 0))
        setup["trade_value_7B_1x"] = tv_max >= 7e9
        # 20일 내 +25% 일일 임펄스
        impulse_max = 0
        for i in range(-20, 0):
            if i - 1 < -len(history):
                continue
            prev_close = history[i - 1]["close"]
            if prev_close > 0:
                ch = (history[i]["close"] - prev_close) / prev_close * 100
                if ch > impulse_max:
                    impulse_max = ch
        setup["impulse_25pct_20d"] = impulse_max >= 25
    else:
        setup["trade_value_7B_1x"] = False
        setup["impulse_25pct_20d"] = False

    # === Fundamentals (DART) ===
    fund = {}
    if financials and financials.get("quarters"):
        q = financials["quarters"]
        # 최근 분기 찾기 (가장 최근 분기 데이터)
        sorted_keys = sorted(q.keys(), reverse=True)
        # 분기 키 형식: "2026_3Q", "2026_2Q" 등
        latest_q_key = None
        prev_q_key = None
        yoy_q_key = None
        for k in sorted_keys:
            year, qname = k.split("_")
            if qname == "Y":
                continue
            if not latest_q_key:
                latest_q_key = k
                # YoY 키 = 같은 분기 작년
                yoy_q_key = f"{int(year)-1}_{qname}"
            elif not prev_q_key:
                prev_q_key = k
                break
        latest_q = q.get(latest_q_key)
        prev_q = q.get(prev_q_key)
        yoy_q = q.get(yoy_q_key)
        # EPS 성장률 (분기 YoY)
        if latest_q and yoy_q and yoy_q.get("EPS", 0) > 0:
            eps_growth = (latest_q["EPS"] - yoy_q["EPS"]) / yoy_q["EPS"] * 100
            fund["eps_growth_q_yoy"] = round(eps_growth, 1)
            fund["eps_growth_25pct"] = eps_growth >= 25
        else:
            fund["eps_growth_q_yoy"] = None
            fund["eps_growth_25pct"] = False
        # EPS 가속화 (이번 Q YoY 성장 > 직전 Q YoY 성장)
        if prev_q and latest_q_key and prev_q_key:
            prev_year = int(prev_q_key.split("_")[0])
            prev_qname = prev_q_key.split("_")[1]
            prev_yoy = q.get(f"{prev_year-1}_{prev_qname}")
            if prev_yoy and prev_yoy.get("EPS", 0) > 0 and prev_q.get("EPS"):
                prev_growth = (prev_q["EPS"] - prev_yoy["EPS"]) / prev_yoy["EPS"] * 100
                fund["eps_accelerating"] = (fund.get("eps_growth_q_yoy") or 0) > prev_growth
            else:
                fund["eps_accelerating"] = False
        else:
            fund["eps_accelerating"] = False
        # 매출 성장률 (분기 YoY)
        if latest_q and yoy_q and yoy_q.get("매출액", 0) > 0:
            sales_growth = (latest_q["매출액"] - yoy_q["매출액"]) / yoy_q["매출액"] * 100
            fund["sales_growth_q_yoy"] = round(sales_growth, 1)
            fund["sales_growth_15pct"] = sales_growth >= 15
        else:
            fund["sales_growth_q_yoy"] = None
            fund["sales_growth_15pct"] = False
        # 영업이익률 분기
        fund["op_margin_q"] = latest_q["영업이익률"] if latest_q else 0
        fund["op_margin_q_10pct"] = fund["op_margin_q"] >= 10
        # 영업이익률 결산
        annuals = [q[k] for k in q if k.endswith("_Y")]
        if annuals:
            latest_annual = annuals[0]
            fund["op_margin_annual"] = latest_annual["영업이익률"]
            fund["op_margin_annual_10pct"] = fund["op_margin_annual"] >= 10
            # 3년 평균
            recent_3 = annuals[:3]
            margins = [a["영업이익률"] for a in recent_3 if a["영업이익률"] > 0]
            if margins:
                fund["op_margin_3y_avg"] = round(sum(margins) / len(margins), 2)
                fund["op_margin_3y_avg_20pct"] = fund["op_margin_3y_avg"] >= 20
            else:
                fund["op_margin_3y_avg"] = 0
                fund["op_margin_3y_avg_20pct"] = False
        else:
            fund["op_margin_annual"] = 0
            fund["op_margin_annual_10pct"] = False
            fund["op_margin_3y_avg"] = 0
            fund["op_margin_3y_avg_20pct"] = False
    else:
        fund = {
            "eps_growth_q_yoy": None, "eps_growth_25pct": False,
            "eps_accelerating": False,
            "sales_growth_q_yoy": None, "sales_growth_15pct": False,
            "op_margin_q": 0, "op_margin_q_10pct": False,
            "op_margin_annual": 0, "op_margin_annual_10pct": False,
            "op_margin_3y_avg": 0, "op_margin_3y_avg_20pct": False,
        }

    # === 점수 계산 ===
    # Trend Template 8 (40점 만점, 5점씩)
    tt_score = sum([
        tt["price_above_ma50"], tt["price_above_ma150"], tt["price_above_ma200"],
        tt["ma50_above_ma150"], tt["ma150_above_ma200"],
        tt["ma200_uptrend"], tt["within_25pct_of_52w_high"], tt["rs_rating_70plus"],
    ]) * 5
    # Setup 4 (20점 만점)
    setup_score = sum([
        setup["5day_tightness_10pct"], setup["5day_open_close_5pct"],
        setup["trade_value_7B_1x"], tt["above_25pct_from_52w_low"],
    ]) * 5
    # Fundamentals 6 (40점 만점)
    fund_score = sum([
        fund["eps_growth_25pct"], fund["eps_accelerating"], fund["sales_growth_15pct"],
        fund["op_margin_q_10pct"], fund["op_margin_annual_10pct"], fund["op_margin_3y_avg_20pct"],
    ]) * (40 / 6)
    total_score = round(tt_score + setup_score + fund_score, 1)

    # Trend Template 8개 모두 통과해야 진짜 미너비니 후보
    tt_passed = sum([
        tt["price_above_ma50"], tt["price_above_ma150"], tt["price_above_ma200"],
        tt["ma50_above_ma150"], tt["ma150_above_ma200"],
        tt["ma200_uptrend"], tt["within_25pct_of_52w_high"], tt["rs_rating_70plus"],
    ])
    fund_passed = sum([
        fund["eps_growth_25pct"], fund["sales_growth_15pct"],
        fund["op_margin_3y_avg_20pct"],
    ])

    return {
        "eligible": True,
        "trend_template": tt,
        "setup": setup,
        "fundamentals": fund,
        "tt_score": tt_score,
        "setup_score": setup_score,
        "fund_score": round(fund_score, 1),
        "total_score": total_score,
        "tt_passed_count": tt_passed,
        "fund_passed_count": fund_passed,
        "minervini_strict": tt_passed >= 8,
        "minervini_strong": tt_passed >= 6 and fund_passed >= 2,
        "current_price": cur_close,
        "high_52w": high_52w,
        "low_52w": low_52w,
        "ma50": ma50,
        "ma150": ma150,
        "ma200": ma200,
    }


# ================================
# Telegram 알림
# ================================

def send_telegram(bot_token, chat_id, text):
    """Telegram sendMessage."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        r = requests.post(
            url,
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown", "disable_web_page_preview": True},
            timeout=10,
        )
        if r.status_code == 200:
            return True, "OK"
        try:
            err = r.json().get("description", r.text[:200])
        except Exception:
            err = r.text[:200]
        return False, f"HTTP {r.status_code}: {err}"
    except Exception as e:
        return False, f"Exception: {e}"


def notify_new_minervini(results):
    """이전에 알린 적 없는 신규 strict/strong 종목 → Telegram 알림.
    중복 방지: data/screener_alerted.json에 (code, category) 누적.
    """
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        print("  TELEGRAM_BOT_TOKEN/CHAT_ID env not set (skip alerts)")
        return

    alerted_path = Path("data/screener_alerted.json")
    alerted = {"strict": [], "strong": []}
    if alerted_path.exists():
        try:
            alerted = json.loads(alerted_path.read_text(encoding="utf-8"))
            alerted.setdefault("strict", [])
            alerted.setdefault("strong", [])
        except Exception:
            pass
    strict_set = set(alerted["strict"])
    strong_set = set(alerted["strong"])

    new_strict = []
    new_strong = []
    for r in results:
        code = r.get("code")
        if not code:
            continue
        if r.get("minervini_strict") and code not in strict_set:
            new_strict.append(r)
            strict_set.add(code)
            strong_set.add(code)  # strict는 strong도 자동 충족
        elif r.get("minervini_strong") and code not in strong_set:
            new_strong.append(r)
            strong_set.add(code)

    if not new_strict and not new_strong:
        print("  no new minervini candidates (skip telegram)")
        return

    today = datetime.now(KST).strftime("%Y-%m-%d")
    lines = [f"🎯 *미너비니 신규 진입* — {today}\n"]

    def fmt_one(r):
        tt_pass = r.get("tt_passed_count", 0)
        fund_pass = r.get("fund_passed_count", 0)
        score = r.get("total_score", 0)
        rs = (r.get("trend_template") or {}).get("_rs_value")
        rs_txt = f" · RS {rs}" if rs else ""
        ch = r.get("change", 0)
        sign = "+" if ch > 0 else ""
        price = r.get("price", 0)
        return (
            f"• *{r['name']}* (`{r['code']}` {r.get('market','')})\n"
            f"  {price:,}원 ({sign}{ch:.2f}%) · TT {tt_pass}/8 · F {fund_pass}/3{rs_txt} · 점수 *{score}*"
        )

    if new_strict:
        lines.append(f"*🏆 엄격 통과 (8/8 Trend Template) — {len(new_strict)}개*")
        for r in new_strict[:10]:
            lines.append(fmt_one(r))
        if len(new_strict) > 10:
            lines.append(f"... 외 {len(new_strict) - 10}개")
        lines.append("")
    if new_strong:
        lines.append(f"*⭐ 우량 (6+/8 + 펀더멘털) — {len(new_strong)}개*")
        for r in new_strong[:10]:
            lines.append(fmt_one(r))
        if len(new_strong) > 10:
            lines.append(f"... 외 {len(new_strong) - 10}개")

    msg = "\n".join(lines)
    ok, info = send_telegram(bot_token, chat_id, msg)
    if ok:
        # 발송 성공 시에만 캐시 업데이트
        alerted["strict"] = sorted(strict_set)
        alerted["strong"] = sorted(strong_set)
        alerted["last_sent"] = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")
        alerted_path.write_text(json.dumps(alerted, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  ✅ telegram sent: strict {len(new_strict)} new, strong {len(new_strong)} new")
    else:
        print(f"  ❌ telegram failed: {info}")


# ================================
# 메인
# ================================

def main():
    print(f"=== Minervini Screener — {datetime.now(KST).strftime('%Y-%m-%d %H:%M KST')} ===")

    # 1. market.json 로드 (현재 시세)
    market_path = Path("data/market.json")
    if not market_path.exists():
        print("ERROR: data/market.json not found. Run fetch_prices.py first.")
        sys.exit(1)
    market = json.loads(market_path.read_text(encoding="utf-8"))
    stocks = market.get("stocks", {})
    print(f"  loaded {len(stocks)} stocks from market.json")

    # 2. 1차 필터: 거래대금 + 가격 > 0
    candidates = {}
    for code, s in stocks.items():
        if s.get("price", 0) <= 0 or s.get("volume", 0) <= 1000:
            continue
        tv = s["price"] * s["volume"]
        if tv < 1e9:  # 10억 미만 거래대금 제외
            continue
        candidates[code] = s
    print(f"  Step 1: {len(candidates)} stocks pass liquidity filter (거래대금 10억+)")

    # 거래대금 상위 1000개로 제한 (DART 호출 제한 고려)
    sorted_candidates = sorted(
        candidates.items(),
        key=lambda x: x[1]["price"] * x[1]["volume"],
        reverse=True,
    )[:1000]
    candidate_codes = [c for c, _ in sorted_candidates]
    print(f"  Step 2: top {len(candidate_codes)} by trading value")

    # 3. DART 재무 데이터 (캐시 활용)
    print("\n[DART] 재무 데이터 수집...")
    if DART_KEY:
        corp_codes = fetch_dart_corp_codes()
        financials_cache = fetch_all_quarterly_data(corp_codes, candidate_codes)
    else:
        print("  DART_API_KEY 없음 - 재무 조건은 평가 안 됨")
        financials_cache = {}

    # 4. 가격 히스토리 (252일)
    print("\n[Naver] 252일 OHLC 수집...")
    histories = fetch_all_stock_history(candidate_codes, days=252)

    # 5. 지수 history (RS Rating용)
    market_index_history = []
    if market.get("indices", {}).get("kospi", {}).get("history"):
        market_index_history = [h["close"] for h in market["indices"]["kospi"]["history"]]
    print(f"  market index history: {len(market_index_history)} days")

    # 6. 평가
    print("\n[평가] 미너비니 조건 적용...")
    results = []
    for code in candidate_codes:
        history = histories.get(code)
        if not history or len(history) < 220:
            continue
        closes = [h["close"] for h in history]
        # market history를 종목 history와 align (같은 길이로)
        m_hist = market_index_history[-len(closes):] if market_index_history else []
        evaluation = evaluate_minervini(code, history, financials_cache.get(code), m_hist)
        if not evaluation.get("eligible"):
            continue
        s = candidates.get(code, {})
        results.append({
            "code": code,
            "name": s.get("name", code),
            "market": s.get("market", ""),
            "price": s.get("price", 0),
            "change": s.get("change", 0),
            **evaluation,
        })

    # 7. 정렬: total_score 높은 순
    results.sort(key=lambda x: x["total_score"], reverse=True)
    strict_count = sum(1 for r in results if r.get("minervini_strict"))
    strong_count = sum(1 for r in results if r.get("minervini_strong"))
    print(f"  evaluated {len(results)} stocks: strict {strict_count}, strong {strong_count}")

    # 8. 저장
    out_path = Path("data/screener_results.json")
    out_path.write_text(json.dumps({
        "updated": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        "trading_day": datetime.now(KST).strftime("%Y%m%d"),
        "total_evaluated": len(results),
        "minervini_strict_count": strict_count,
        "minervini_strong_count": strong_count,
        "results": results[:100],  # top 100 저장
    }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"\n✅ Saved screener_results.json ({len(results[:100])} stocks)")

    # 9. Telegram 알림 (신규 strict/strong만)
    print("\n[Telegram] 신규 미너비니 종목 알림...")
    notify_new_minervini(results)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
