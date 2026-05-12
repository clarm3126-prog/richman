#!/usr/bin/env python3
"""신규 모멘텀 스크리너 — Stage 1 → Stage 2 전환 종목 탐지.

미너비니의 "Stage 2 초입" 잡기 + 한국 시장 자금흐름 결합.

3계층:
1. 기술적 선행 신호 (60점): MA200 막 돌파, VCP, Tight Action, Volume Surge,
   Pivot Breakout, Higher Lows
2. 자금흐름 (20점): 거래량 surge, 테마 ranking 급상승
3. 펀더멘털 가속화 (20점): EPS YoY 가속화

출력: data/momentum_results.json + 신규 진입 종목 Telegram 알림
"""
import concurrent.futures
import json
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

import pytz
import requests

KST = pytz.timezone("Asia/Seoul")
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; MomentumScreener/1.0)"}

# 같은 디렉토리의 screener.py 함수 재사용
sys.path.insert(0, str(Path(__file__).parent))
from screener import (
    fetch_all_stock_history,
    sma, send_telegram,
    load_metadata, is_pump_or_warning, DEDUP_RESET_DAYS,
)


# ================================
# 테마 모멘텀 (data/history 활용)
# ================================

def load_recent_history(days=10):
    """data/history/{date}.json 최근 N일 로드."""
    hist_dir = Path("data/history")
    if not hist_dir.exists():
        return []
    files = sorted(
        [f for f in hist_dir.glob("*.json") if f.stem != "index"],
        reverse=True,
    )[:days]
    out = []
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            data["_date"] = f.stem
            out.append(data)
        except Exception:
            pass
    return out


def compute_rising_themes(history_list, top_n=20):
    """최근 5일 평균 vs 그 이전 5일 평균 비교. 강세 전환 테마 top_n 추출."""
    if len(history_list) < 6:
        return []
    recent = history_list[:5]  # most recent 5
    older = history_list[5:10] if len(history_list) >= 10 else history_list[5:]
    if not older:
        return []
    # 테마 이름 → 평균 ranking (낮을수록 좋음)
    def avg_rank_by_name(snapshots):
        rank_sum = {}
        rank_cnt = {}
        for snap in snapshots:
            for i, t in enumerate(snap.get("themes", []) or []):
                name = t.get("name")
                if not name:
                    continue
                rank_sum[name] = rank_sum.get(name, 0) + (i + 1)
                rank_cnt[name] = rank_cnt.get(name, 0) + 1
        return {n: rank_sum[n] / rank_cnt[n] for n in rank_sum}
    recent_rank = avg_rank_by_name(recent)
    older_rank = avg_rank_by_name(older)
    rising = []
    for name, r_rank in recent_rank.items():
        o_rank = older_rank.get(name, 100)  # 없으면 100위로 가정
        delta = o_rank - r_rank  # 양수일수록 ranking 상승
        if delta >= 5:  # 5위 이상 상승
            rising.append({"name": name, "recent_rank": round(r_rank, 1),
                          "old_rank": round(o_rank, 1), "delta": round(delta, 1)})
    rising.sort(key=lambda x: x["delta"], reverse=True)
    return rising[:top_n]


def fetch_theme_members(theme_no):
    """단일 테마 멤버 종목 코드 set."""
    url = f"https://m.stock.naver.com/api/stocks/theme/{theme_no}?page=1&pageSize=50"
    headers = {**HEADERS, "Referer": "https://m.stock.naver.com/"}
    try:
        r = requests.get(url, headers=headers, timeout=8)
        if r.status_code != 200:
            return set()
        data = r.json()
        return {
            str(s.get("itemCode")).zfill(6)
            for s in data.get("stocks", [])
            if s.get("itemCode")
        }
    except Exception:
        return set()


def get_rising_theme_stocks(rising_themes, market):
    """강세 전환 테마들의 멤버 종목 코드 → 가중치 매핑.
    여러 테마에 속하면 가중치 누적.
    """
    # market.naver_themes에서 no 매핑
    theme_no_map = {}
    for t in market.get("naver_themes", []) or []:
        theme_no_map[t.get("name")] = t.get("no")

    stock_weights = {}
    for rt in rising_themes:
        no = theme_no_map.get(rt["name"])
        if not no:
            continue
        members = fetch_theme_members(no)
        # 가중치: ranking 상승폭 / 10 (5위 상승 = 0.5)
        weight = rt["delta"] / 10
        for code in members:
            stock_weights[code] = stock_weights.get(code, 0) + weight
        time.sleep(0.05)
    return stock_weights


# ================================
# 모멘텀 평가
# ================================

def evaluate_momentum(stock_code, history, financials, theme_weight):
    """단일 종목 모멘텀 시그널 평가."""
    if len(history) < 220:
        return {"eligible": False, "reason": f"history {len(history)} < 220"}

    closes = [h["close"] for h in history]
    highs = [h["high"] for h in history]
    lows = [h["low"] for h in history]
    volumes = [h["volume"] for h in history]
    cur_close = closes[-1]

    # === Signal 1: MA200 막 돌파 (Stage 1→2 전환 핵심) ===
    ma200_cross_recent = False
    cross_days_ago = None
    # i=1 (오늘) 부터 i=15 (15일 전) — 가장 최근 cross를 찾음
    for i in range(1, 16):
        if len(closes) < 200 + i:
            break
        # i일 전 ma200과 i일 전 종가
        if i == 0:
            ma_then = sum(closes[-200:]) / 200  # 오늘 ma200
        else:
            ma_then = sum(closes[-(200 + i):-i]) / 200
        ma_prev_day = sum(closes[-(201 + i):-(i + 1)]) / 200 if len(closes) >= 201 + i else None
        close_then = closes[-i] if i > 0 else closes[-1]
        close_prev = closes[-(i + 1)] if len(closes) > i else None
        if ma_prev_day and close_prev and close_prev <= ma_prev_day and close_then > ma_then:
            ma200_cross_recent = True
            cross_days_ago = i
            break
    # 또한 현재도 MA200 위에 있어야 함
    cur_ma200 = sma(closes, 200)
    above_ma200_now = cur_close > cur_ma200 if cur_ma200 else False
    ma200_signal = ma200_cross_recent and above_ma200_now

    # === Signal 2: VCP (3주간 변동폭 축소) ===
    vcp_contracting = False
    if len(history) >= 60:
        # 5일 단위로 4구간의 변동폭 비교
        ranges = []
        for i in range(4):
            start = -((i + 1) * 5)
            end = -(i * 5) if i > 0 else None
            window = closes[start:end] if end else closes[start:]
            if len(window) >= 5:
                w_high = max(window)
                w_low = min(window)
                if w_low > 0:
                    ranges.append((w_high - w_low) / w_low * 100)
        # 가장 최근 5일 변동폭이 가장 오래된 5일 변동폭의 60% 이하
        if len(ranges) >= 4:
            vcp_contracting = ranges[0] < ranges[-1] * 0.6 and ranges[0] < ranges[1]

    # === Signal 3: Tight Action (최근 10일 변동폭 8% 이하) ===
    tight_action = False
    if len(highs) >= 10:
        last10_high = max(highs[-10:])
        last10_low = min(lows[-10:])
        if last10_low > 0:
            tight_action = (last10_high - last10_low) / last10_low * 100 <= 8

    # === Signal 4: Volume Surge (5일 평균 vs 20일 평균) ===
    volume_surge_2x = False
    volume_surge_15x = False
    vol_ratio = 0
    if len(volumes) >= 25:
        vol_5d = sum(volumes[-5:]) / 5
        vol_20d_prev = sum(volumes[-25:-5]) / 20
        if vol_20d_prev > 0:
            vol_ratio = vol_5d / vol_20d_prev
            volume_surge_2x = vol_ratio >= 2
            volume_surge_15x = vol_ratio >= 1.5

    # === Signal 5: Pivot Point Breakout (20일 고점 돌파) ===
    pivot_breakout = False
    if len(highs) >= 23:
        prev_20d_high = max(highs[-23:-3])
        recent_3d_high = max(highs[-3:])
        if prev_20d_high > 0:
            pivot_breakout = recent_3d_high >= prev_20d_high * 1.02

    # === Signal 6: Higher Lows (상승 추세 형성) ===
    higher_lows = False
    if len(lows) >= 30:
        low_first_half = min(lows[-30:-15])
        low_second_half = min(lows[-15:])
        higher_lows = low_second_half > low_first_half

    # === Signal 7: 거래대금 충분 (30억+) ===
    trade_value = cur_close * volumes[-1] if volumes else 0
    liquidity_ok = trade_value >= 3e9

    # === Signal 8: 테마 모멘텀 (속한 테마가 ranking 상승 중) ===
    theme_rising = theme_weight >= 0.5

    # === Signal 9: Pocket Pivot (Minervini's institutional accumulation signal) ===
    # 정의: 오늘이 up day인데 거래량이 최근 10일 down day 거래량 max 이상
    # → 기관 매집 신호. (5% 상한 제거 - Minervini 본인 정의에 없음)
    pocket_pivot = False
    if len(history) >= 11 and len(volumes) >= 11:
        today_change_pct = (closes[-1] - closes[-2]) / closes[-2] * 100 if closes[-2] > 0 else 0
        if today_change_pct > 0:
            # 최근 10일 down day 중 최대 거래량
            down_volumes_10d = []
            for i in range(-11, -1):
                day_chg = (closes[i] - closes[i - 1]) / closes[i - 1] * 100 if closes[i - 1] > 0 else 0
                if day_chg < 0:
                    down_volumes_10d.append(volumes[i])
            if down_volumes_10d:
                max_down_vol = max(down_volumes_10d)
                if volumes[-1] >= max_down_vol:
                    pocket_pivot = True

    # === Fundamental: EPS 가속화 ===
    eps_accelerating = False
    eps_growth_recent = None
    eps_growth_prev = None
    if financials and financials.get("quarters"):
        q = financials["quarters"]
        sorted_keys = sorted(q.keys(), reverse=True)
        latest_q_key = None
        prev_q_key = None
        for k in sorted_keys:
            year, qname = k.split("_")
            if qname == "Y":
                continue
            if not latest_q_key:
                latest_q_key = k
            elif not prev_q_key:
                prev_q_key = k
                break
        # latest YoY
        if latest_q_key:
            year, qname = latest_q_key.split("_")
            yoy_key = f"{int(year)-1}_{qname}"
            latest_q = q.get(latest_q_key)
            yoy_q = q.get(yoy_key)
            if latest_q and yoy_q and yoy_q.get("EPS", 0) > 0:
                eps_growth_recent = (latest_q["EPS"] - yoy_q["EPS"]) / yoy_q["EPS"] * 100
        # prev quarter YoY
        if prev_q_key:
            year, qname = prev_q_key.split("_")
            prev_yoy_key = f"{int(year)-1}_{qname}"
            prev_q = q.get(prev_q_key)
            prev_yoy_q = q.get(prev_yoy_key)
            if prev_q and prev_yoy_q and prev_yoy_q.get("EPS", 0) > 0:
                eps_growth_prev = (prev_q["EPS"] - prev_yoy_q["EPS"]) / prev_yoy_q["EPS"] * 100
        # 가속화: 최근 분기 YoY > 직전 분기 YoY (둘 다 양수)
        if eps_growth_recent is not None and eps_growth_prev is not None:
            eps_accelerating = (
                eps_growth_recent > eps_growth_prev
                and eps_growth_recent > 10
            )

    # === 점수 계산 (총 100점) ===
    technical_signals = [
        ma200_signal, vcp_contracting, tight_action,
        volume_surge_2x, pivot_breakout, higher_lows,
    ]
    technical_score = sum(technical_signals) * 10  # 60점 만점
    flow_score = (volume_surge_15x * 10) + (theme_rising * 10) + (pocket_pivot * 5)  # 25점
    fund_score = eps_accelerating * 20  # 20점
    total = technical_score + flow_score + fund_score

    # 모멘텀 강세 후보: MA200 막 돌파 + 추가 신호 2개+ + 유동성
    momentum_strong = (
        ma200_signal
        and sum(technical_signals) >= 3
        and liquidity_ok
        and (volume_surge_15x or theme_rising or pivot_breakout)
    )
    # 사전 진입 후보 (VCP 완성 + Pocket Pivot OR Tight): 폭발 직전 매집 의심
    pre_breakout = (
        liquidity_ok and higher_lows
        and (
            (vcp_contracting and tight_action and not pivot_breakout)
            or (pocket_pivot and tight_action)
        )
    )

    return {
        "eligible": True,
        "ma200_cross_recent": ma200_signal,
        "ma200_cross_days_ago": cross_days_ago if ma200_signal else None,
        "vcp_contracting": vcp_contracting,
        "tight_action": tight_action,
        "volume_surge_2x": volume_surge_2x,
        "volume_surge_15x": volume_surge_15x,
        "vol_ratio": round(vol_ratio, 2),
        "pivot_breakout": pivot_breakout,
        "higher_lows": higher_lows,
        "liquidity_ok": liquidity_ok,
        "theme_rising": theme_rising,
        "theme_weight": round(theme_weight, 2),
        "pocket_pivot": pocket_pivot,
        "eps_growth_recent": round(eps_growth_recent, 1) if eps_growth_recent is not None else None,
        "eps_growth_prev": round(eps_growth_prev, 1) if eps_growth_prev is not None else None,
        "eps_accelerating": eps_accelerating,
        "technical_score": technical_score,
        "flow_score": flow_score,
        "fund_score": fund_score,
        "total_score": total,
        "momentum_strong": momentum_strong,
        "pre_breakout": pre_breakout,
        "current_price": cur_close,
        "trade_value": trade_value,
    }


# ================================
# Telegram 알림
# ================================

def notify_new_momentum(results):
    """이전에 알린 적 없는 신규 momentum_strong/pre_breakout 종목 알림."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        print("  TELEGRAM_BOT_TOKEN/CHAT_ID env not set (skip alerts)")
        return

    alerted_path = Path("data/momentum_alerted.json")
    alerted = {"strong": {}, "pre_breakout": {}}
    if alerted_path.exists():
        try:
            raw = json.loads(alerted_path.read_text(encoding="utf-8"))
            for key in ["strong", "pre_breakout"]:
                v = raw.get(key, {})
                if isinstance(v, list):
                    today_str = datetime.now(KST).strftime("%Y-%m-%d")
                    alerted[key] = {c: today_str for c in v}
                elif isinstance(v, dict):
                    alerted[key] = v
                else:
                    alerted[key] = {}
        except Exception:
            pass
    today = datetime.now(KST)
    cutoff = today.timestamp() - DEDUP_RESET_DAYS * 86400
    def active_set(d):
        out = set()
        for code, date_str in d.items():
            try:
                ts = datetime.strptime(date_str, "%Y-%m-%d").timestamp()
                if ts >= cutoff:
                    out.add(code)
            except Exception:
                pass
        return out
    strong_set = active_set(alerted["strong"])
    pre_set = active_set(alerted["pre_breakout"])

    new_strong = []
    new_pre = []
    for r in results:
        code = r.get("code")
        if not code:
            continue
        if r.get("momentum_strong") and code not in strong_set:
            new_strong.append(r)
            strong_set.add(code)
        elif r.get("pre_breakout") and code not in pre_set:
            new_pre.append(r)
            pre_set.add(code)

    if not new_strong and not new_pre:
        print("  no new momentum candidates (skip telegram)")
        return

    today = datetime.now(KST).strftime("%Y-%m-%d")
    lines = [f"🚀 *신규 모멘텀 진입* — {today}\n"]

    def fmt_one(r):
        score = r.get("total_score", 0)
        days_ago = r.get("ma200_cross_days_ago")
        cross_txt = f"MA200 {days_ago}일 전 돌파" if days_ago else ""
        vol_txt = f"거래량 {r.get('vol_ratio', 0):.1f}x" if r.get('vol_ratio', 0) >= 1.5 else ""
        eps_g = r.get("eps_growth_recent")
        eps_txt = f"EPS YoY {eps_g}%" if eps_g and r.get("eps_accelerating") else ""
        signals = " · ".join(s for s in [cross_txt, vol_txt, eps_txt] if s)
        ch = r.get("change", 0)
        sign = "+" if ch > 0 else ""
        price = r.get("price", 0)
        return (
            f"• *{r['name']}* (`{r['code']}` {r.get('market','')})\n"
            f"  {price:,}원 ({sign}{ch:.2f}%) · 점수 *{score}*\n"
            f"  {signals}"
        )

    if new_strong:
        lines.append(f"*🔥 모멘텀 강세 — {len(new_strong)}개*")
        for r in new_strong[:10]:
            lines.append(fmt_one(r))
        if len(new_strong) > 10:
            lines.append(f"... 외 {len(new_strong) - 10}개")
        lines.append("")
    if new_pre:
        lines.append(f"*⏳ 사전 진입 후보 (VCP 완성) — {len(new_pre)}개*")
        for r in new_pre[:10]:
            lines.append(fmt_one(r))
        if len(new_pre) > 10:
            lines.append(f"... 외 {len(new_pre) - 10}개")

    msg = "\n".join(lines)
    ok, info = send_telegram(bot_token, chat_id, msg)
    if ok:
        today_str = datetime.now(KST).strftime("%Y-%m-%d")
        for r in new_strong:
            alerted["strong"][r["code"]] = today_str
        for r in new_pre:
            alerted["pre_breakout"][r["code"]] = today_str
        alerted["last_sent"] = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")
        alerted_path.write_text(json.dumps(alerted, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  ✅ telegram sent: strong {len(new_strong)} new, pre_breakout {len(new_pre)} new")
    else:
        print(f"  ❌ telegram failed: {info}")


# ================================
# 메인
# ================================

def main():
    print(f"=== Momentum Screener — {datetime.now(KST).strftime('%Y-%m-%d %H:%M KST')} ===")

    # 1. market.json 로드
    market_path = Path("data/market.json")
    if not market_path.exists():
        print("ERROR: data/market.json not found.")
        sys.exit(1)
    market = json.loads(market_path.read_text(encoding="utf-8"))
    stocks = market.get("stocks", {})
    print(f"  loaded {len(stocks)} stocks from market.json")

    # 2. 시장 컨텍스트 필터: KOSPI가 MA50 위에 있어야 (약세장에선 false signal 多)
    kospi_history = market.get("indices", {}).get("kospi", {}).get("history", [])
    market_bullish = True
    if len(kospi_history) >= 50:
        kospi_closes = [h["close"] for h in kospi_history]
        kospi_ma50 = sum(kospi_closes[-50:]) / 50
        market_bullish = kospi_closes[-1] > kospi_ma50
        print(f"  KOSPI: {kospi_closes[-1]:.2f} vs MA50 {kospi_ma50:.2f} → {'강세' if market_bullish else '약세'}")
    if not market_bullish:
        print("  ⚠️ 시장 약세장 — 신호 신뢰도 낮음 (그래도 평가는 진행)")

    # 3. 1차 필터: 거래대금 + 가격
    candidates = {}
    for code, s in stocks.items():
        if s.get("price", 0) <= 0 or s.get("volume", 0) <= 1000:
            continue
        tv = s["price"] * s["volume"]
        if tv < 3e9:  # 30억 미만 거래대금 제외
            continue
        candidates[code] = s
    print(f"  Step 1: {len(candidates)} stocks pass liquidity (거래대금 30억+)")

    # 거래대금 상위 1000개로 제한
    sorted_candidates = sorted(
        candidates.items(),
        key=lambda x: x[1]["price"] * x[1]["volume"],
        reverse=True,
    )[:1000]
    candidate_codes = [c for c, _ in sorted_candidates]
    print(f"  Step 2: top {len(candidate_codes)} by trading value")

    # 4. 테마 모멘텀: 최근 강세 전환 테마 + 멤버 종목
    print("\n[테마 모멘텀] 최근 5일 vs 5일 ranking 변화 분석...")
    history_list = load_recent_history(days=10)
    print(f"  loaded {len(history_list)} historical snapshots")
    rising_themes = compute_rising_themes(history_list, top_n=20)
    print(f"  rising themes ({len(rising_themes)}):", [t["name"] for t in rising_themes[:5]])
    rising_stocks_weights = get_rising_theme_stocks(rising_themes, market) if rising_themes else {}
    print(f"  rising theme members: {len(rising_stocks_weights)} stocks tagged")

    # 5. DART 재무 데이터 (캐시 활용 — screener.py가 만든 것)
    financials_cache = {}
    fin_path = Path("data/dart_financials.json")
    if fin_path.exists():
        try:
            financials_cache = json.loads(fin_path.read_text(encoding="utf-8"))
            print(f"  loaded {len(financials_cache)} financial records (cache)")
        except Exception:
            pass
    else:
        print("  ⚠️ data/dart_financials.json not found — EPS 가속화 시그널 비활성")

    # 5.5 작전주/펌프 필터 metadata 로드
    metadata = load_metadata()
    meta_stocks = metadata.get("stocks", {})
    warning_set = set(metadata.get("warning_stocks", []))
    print(f"  metadata loaded: {len(meta_stocks)} stocks, {len(warning_set)} warnings")

    # 6. OHLC 252일 fetch
    print("\n[Naver] 252일 OHLC 수집...")
    histories = fetch_all_stock_history(candidate_codes, days=252)

    # 7. 평가
    print("\n[평가] 모멘텀 시그널 적용...")
    results = []
    skipped_pump = 0
    skip_reasons = {}
    for code in candidate_codes:
        history = histories.get(code)
        if not history or len(history) < 220:
            continue
        # 작전주/펌프/관리종목 필터
        skip, reason = is_pump_or_warning(code, meta_stocks, warning_set, history)
        if skip:
            skipped_pump += 1
            skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
            continue
        theme_w = rising_stocks_weights.get(code, 0)
        evaluation = evaluate_momentum(code, history, financials_cache.get(code), theme_w)
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
    if skipped_pump:
        print(f"  filtered out {skipped_pump} stocks: {skip_reasons}")

    # 8. 정렬
    results.sort(key=lambda x: x["total_score"], reverse=True)
    strong_count = sum(1 for r in results if r.get("momentum_strong"))
    pre_count = sum(1 for r in results if r.get("pre_breakout"))
    print(f"  evaluated {len(results)} stocks: strong {strong_count}, pre_breakout {pre_count}")

    # 9. 저장 — 카테고리 통과 종목은 무조건 포함, 나머지는 score 순
    must_include = [r for r in results if r.get("momentum_strong") or r.get("pre_breakout")]
    must_codes = {r["code"] for r in must_include}
    others = [r for r in results if r["code"] not in must_codes]
    # must_include는 모두 + 나머지는 score 상위 (총 200까지)
    to_save = must_include + others[: max(0, 200 - len(must_include))]
    to_save.sort(key=lambda x: x["total_score"], reverse=True)
    out_path = Path("data/momentum_results.json")
    out_path.write_text(json.dumps({
        "updated": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        "trading_day": datetime.now(KST).strftime("%Y%m%d"),
        "market_bullish": market_bullish,
        "rising_themes": rising_themes,
        "total_evaluated": len(results),
        "momentum_strong_count": strong_count,
        "pre_breakout_count": pre_count,
        "results": to_save,
    }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"\n✅ Saved momentum_results.json ({len(to_save)} stocks: must_include {len(must_include)} + others {len(to_save) - len(must_include)})")

    # 10. Telegram 알림
    print("\n[Telegram] 신규 모멘텀 종목 알림...")
    notify_new_momentum(results)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
