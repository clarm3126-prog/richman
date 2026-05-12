#!/usr/bin/env python3
"""테마 강세 예측 — 신뢰도 우선 설계.

현재 활성 시그널 (검증된 것만):
1. 모멘텀 지속 (40점) - 한국 시장에서 가장 reliable
   - 5일 평균 ranking (낮을수록 가산)
   - 5일 ranking 상승폭
   - 5일 평균 등락률
   - 30일 강세 빈도 (top 20에 며칠)
2. 자금흐름 (30점) - 외국인+기관 오늘 net buy
3. 펀더멘털 (20점) - 테마 멤버 평균 EPS 가속화
4. 신뢰도 가산 (10점) - 멤버 수, 거래대금 충분

자체 검증:
- 매일 forecast를 archive
- 주간 backtest로 적중률 측정
- 데이터 쌓일수록 weights 자동 조정 (Phase 2 활성)

출력:
- data/theme_forecast.json (TOP 20 + 점수 + "왜")
- data/theme_forecast_history/{YYYYMMDD}.json (archive)
"""
import concurrent.futures
import json
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

import pytz
import requests

KST = pytz.timezone("Asia/Seoul")
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ThemeForecast/1.0)"}


# ================================
# 데이터 로더
# ================================

def load_recent_history(days=30):
    """data/history/{date}.json 최근 N일."""
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


def fetch_theme_members(theme_no):
    """단일 테마 멤버 종목."""
    url = f"https://m.stock.naver.com/api/stocks/theme/{theme_no}?page=1&pageSize=50"
    headers = {**HEADERS, "Referer": "https://m.stock.naver.com/"}
    try:
        r = requests.get(url, headers=headers, timeout=8)
        if r.status_code != 200:
            return []
        data = r.json()
        return [
            str(s.get("itemCode")).zfill(6)
            for s in data.get("stocks", [])
            if s.get("itemCode")
        ]
    except Exception:
        return []


def load_theme_members_cached(theme_list, max_workers=10):
    """테마별 멤버 종목 cache 로드 + 미스 항목만 fetch.
    cache: data/theme_members_cache.json (주 1회 자동 refresh)
    """
    cache_path = Path("data/theme_members_cache.json")
    cache = {}
    cache_age = 999
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
            updated = cache.get("_updated", "")
            if updated:
                # date-only 비교로 TZ-naive vs aware 충돌 회피
                today_date = datetime.now(KST).date()
                updated_date = datetime.strptime(updated, "%Y-%m-%d").date()
                cache_age = (today_date - updated_date).days
        except Exception:
            cache = {}

    # 7일 이상 오래되면 전체 refresh
    refresh_all = cache_age >= 7
    if refresh_all:
        print("  cache >= 7d old — full refresh")

    needs_fetch = []
    for t in theme_list:
        no = t.get("no")
        if not no:
            continue
        if refresh_all or no not in cache.get("themes", {}):
            needs_fetch.append(no)

    if needs_fetch:
        print(f"  fetching {len(needs_fetch)} theme members...")
        cache.setdefault("themes", {})
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(fetch_theme_members, no): no for no in needs_fetch}
            for f in concurrent.futures.as_completed(futures):
                no = futures[f]
                members = f.result()
                cache["themes"][no] = members
                time.sleep(0.02)
        cache["_updated"] = datetime.now(KST).strftime("%Y-%m-%d")
        cache_path.write_text(json.dumps(cache, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    return cache.get("themes", {})


def load_dart_financials():
    p = Path("data/dart_financials.json")
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_weights():
    """data/theme_forecast_weights.json — backtest 후 자동 튜닝되는 가중치.
    초기값: 모멘텀 40, 자금흐름 30, 펀더 20, 신뢰도 10
    """
    p = Path("data/theme_forecast_weights.json")
    default = {
        "momentum": 40,
        "money_flow": 30,
        "fundamental": 20,
        "confidence": 10,
        "_calibrated": False,
        "_n_backtest_periods": 0,
    }
    if not p.exists():
        return default
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        # 안전장치: total = 100
        total = d.get("momentum", 0) + d.get("money_flow", 0) + d.get("fundamental", 0) + d.get("confidence", 0)
        if abs(total - 100) > 1:
            return default
        return d
    except Exception:
        return default


# ================================
# 시그널 계산
# ================================

def compute_momentum_signals(history_list, theme_name):
    """5일 평균 ranking, 5일 변화, 5일 평균 등락률, 30일 강세 빈도."""
    # ranking history 추출
    rank_history = []  # [(date, rank, change)]
    for snap in history_list:
        themes = snap.get("themes", []) or []
        for i, t in enumerate(themes):
            if t.get("name") == theme_name:
                rank_history.append({
                    "date": snap.get("_date"),
                    "rank": i + 1,
                    "change": t.get("change", 0),
                })
                break
    if not rank_history:
        return None
    rank_history.sort(key=lambda x: x["date"])

    recent_5 = rank_history[-5:]
    older_5 = rank_history[-10:-5] if len(rank_history) >= 10 else []

    # 1. 5일 평균 ranking (낮을수록 좋음)
    avg_rank_recent = sum(r["rank"] for r in recent_5) / len(recent_5)
    # 2. 5일 ranking 변화
    rank_delta = (sum(r["rank"] for r in older_5) / len(older_5) - avg_rank_recent) if older_5 else 0
    # 3. 5일 평균 등락률
    avg_change = sum(r["change"] for r in recent_5) / len(recent_5)
    # 4. 30일 top 20 빈도
    top20_freq = sum(1 for r in rank_history[-30:] if r["rank"] <= 20) / max(len(rank_history[-30:]), 1)

    return {
        "avg_rank_5d": round(avg_rank_recent, 1),
        "rank_delta_5d": round(rank_delta, 1),
        "avg_change_5d": round(avg_change, 2),
        "top20_freq_30d": round(top20_freq, 2),
    }


def compute_money_flow_signal(theme_members, market_stocks, investor_buy_set, investor_sell_set):
    """테마 멤버 종목들의 외국인+기관 net buy 합계 추정.

    market.investor_top는 거래대금 상위 ~160 종목의 net buy 데이터.
    이 중 테마 멤버에 해당하는 종목들의 합계를 자금흐름 proxy로 사용.
    """
    if not theme_members:
        return {"net_buy_count": 0, "net_buy_amount": 0, "members_in_top": 0}

    member_set = set(theme_members)
    net_buy_count = 0
    net_buy_amount = 0
    members_in_top = 0

    # 외국인+기관 buy/sell 데이터 누적
    for inv_data in investor_buy_set:
        for s in inv_data:
            code = s.get("code")
            if code in member_set:
                net_buy_count += 1
                net_buy_amount += s.get("amount", 0)
                members_in_top += 1
    for inv_data in investor_sell_set:
        for s in inv_data:
            code = s.get("code")
            if code in member_set:
                net_buy_count -= 1
                net_buy_amount -= s.get("amount", 0)
                members_in_top += 1

    return {
        "net_buy_count": net_buy_count,
        "net_buy_amount": net_buy_amount,
        "members_in_top": members_in_top,
    }


def compute_fundamental_signal(theme_members, financials_cache):
    """테마 멤버 평균 EPS YoY 가속화 비율, 영업이익률 평균."""
    if not theme_members:
        return {"avg_eps_growth": None, "accelerating_pct": 0, "avg_op_margin": None}

    eps_growths = []
    accelerating_count = 0
    op_margins = []
    evaluated = 0

    for code in theme_members:
        fin = financials_cache.get(code)
        if not fin or not fin.get("quarters"):
            continue
        q = fin["quarters"]
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
        if not latest_q_key:
            continue
        evaluated += 1
        # latest YoY
        year, qname = latest_q_key.split("_")
        yoy_key = f"{int(year)-1}_{qname}"
        latest_q = q.get(latest_q_key)
        yoy_q = q.get(yoy_key)
        if latest_q and yoy_q and yoy_q.get("EPS", 0) > 0:
            growth = (latest_q["EPS"] - yoy_q["EPS"]) / yoy_q["EPS"] * 100
            eps_growths.append(growth)
            # 가속화 체크
            if prev_q_key:
                py, pq = prev_q_key.split("_")
                prev_yoy = q.get(f"{int(py)-1}_{pq}")
                prev_q = q.get(prev_q_key)
                if prev_q and prev_yoy and prev_yoy.get("EPS", 0) > 0:
                    prev_growth = (prev_q["EPS"] - prev_yoy["EPS"]) / prev_yoy["EPS"] * 100
                    if growth > prev_growth and growth > 10:
                        accelerating_count += 1
        if latest_q and latest_q.get("영업이익률", 0) > 0:
            op_margins.append(latest_q["영업이익률"])

    if evaluated == 0:
        return {"avg_eps_growth": None, "accelerating_pct": 0, "avg_op_margin": None}

    avg_eps = sum(eps_growths) / len(eps_growths) if eps_growths else None
    acc_pct = accelerating_count / evaluated * 100 if evaluated > 0 else 0
    avg_op = sum(op_margins) / len(op_margins) if op_margins else None

    return {
        "avg_eps_growth": round(avg_eps, 1) if avg_eps is not None else None,
        "accelerating_pct": round(acc_pct, 1),
        "avg_op_margin": round(avg_op, 1) if avg_op is not None else None,
        "evaluated_members": evaluated,
    }


def compute_confidence(theme_members, market_stocks):
    """신뢰도 점수 - 멤버 수 + 거래대금 충분."""
    if not theme_members:
        return {"member_count": 0, "liquid_count": 0, "total_trade_value": 0}

    liquid_count = 0
    total_tv = 0
    for code in theme_members:
        s = market_stocks.get(code)
        if not s:
            continue
        tv = s.get("price", 0) * s.get("volume", 0)
        if tv >= 1e9:  # 10억 이상
            liquid_count += 1
            total_tv += tv

    return {
        "member_count": len(theme_members),
        "liquid_count": liquid_count,
        "total_trade_value": total_tv,
    }


# ================================
# 점수 정규화 + 결합
# ================================

def normalize_scores(theme_signals_list, weights):
    """모든 테마 signals → percentile 기반 0~weight 점수로 정규화."""
    if not theme_signals_list:
        return []

    def percentile_score(values, key, max_pts, reverse=False):
        """values를 percentile rank → 0~max_pts.
        - reverse=False(기본): 높은 값 = 좋음 (높은 점수)
        - reverse=True: 낮은 값 = 좋음 (avg_rank처럼)
        - 동률(ties): 같은 값들은 평균 rank 부여 (공정성)
        - None 값: 중간값(median) percentile = 0.5 부여 (penalize 안 함)
        """
        n_total = len(values)
        if n_total == 0:
            return []
        # None 분리
        valid = [(v.get(key), i) for i, v in enumerate(values) if v.get(key) is not None]
        if not valid:
            # 모두 None → 모두 중간값
            return [max_pts * 0.5] * n_total
        # 정렬
        sorted_vals = sorted(valid, key=lambda x: x[0], reverse=not reverse)
        n = len(sorted_vals)
        scores = [None] * n_total
        # 동률 그룹별 평균 rank
        i_pos = 0
        while i_pos < n:
            j = i_pos
            same_val = sorted_vals[i_pos][0]
            while j < n and sorted_vals[j][0] == same_val:
                j += 1
            # i_pos ~ j-1 까지가 동률 그룹
            avg_rank = (i_pos + j - 1) / 2  # 0-indexed 평균 rank
            pct = 1 - (avg_rank / max(n - 1, 1)) if n > 1 else 1.0
            for k in range(i_pos, j):
                _, idx = sorted_vals[k]
                scores[idx] = pct * max_pts
            i_pos = j
        # None은 median percentile = 0.5
        for i in range(n_total):
            if scores[i] is None:
                scores[i] = max_pts * 0.5
        return scores

    n_themes = len(theme_signals_list)
    momentum_w = weights.get("momentum", 40)
    flow_w = weights.get("money_flow", 30)
    fund_w = weights.get("fundamental", 20)
    conf_w = weights.get("confidence", 10)

    # === Momentum (4 sub-signals each get equal share) ===
    sub = momentum_w / 4
    # avg_rank_5d: lower is better
    rank_scores = percentile_score(theme_signals_list, "avg_rank_5d", sub, reverse=True)
    # rank_delta_5d: higher is better (positive means ranking improved)
    delta_scores = percentile_score(theme_signals_list, "rank_delta_5d", sub)
    # avg_change_5d: higher is better
    change_scores = percentile_score(theme_signals_list, "avg_change_5d", sub)
    # top20_freq_30d: higher is better
    freq_scores = percentile_score(theme_signals_list, "top20_freq_30d", sub)

    # === Money flow ===
    flow_scores = percentile_score(theme_signals_list, "net_buy_amount", flow_w)

    # === Fundamental ===
    sub_f = fund_w / 2
    eps_scores = percentile_score(theme_signals_list, "avg_eps_growth", sub_f)
    acc_scores = percentile_score(theme_signals_list, "accelerating_pct", sub_f)

    # === Confidence ===
    sub_c = conf_w / 2
    member_scores = percentile_score(theme_signals_list, "liquid_count", sub_c)
    tv_scores = percentile_score(theme_signals_list, "total_trade_value", sub_c)

    out = []
    for i in range(n_themes):
        m_score = round(rank_scores[i] + delta_scores[i] + change_scores[i] + freq_scores[i], 1)
        f_score = round(flow_scores[i], 1)
        u_score = round(eps_scores[i] + acc_scores[i], 1)
        c_score = round(member_scores[i] + tv_scores[i], 1)
        total = round(m_score + f_score + u_score + c_score, 1)
        out.append({
            "momentum_score": m_score,
            "flow_score": f_score,
            "fundamental_score": u_score,
            "confidence_score": c_score,
            "total_score": total,
        })
    return out


# ================================
# 메인
# ================================

def main():
    print(f"=== Theme Forecast — {datetime.now(KST).strftime('%Y-%m-%d %H:%M KST')} ===")

    market_path = Path("data/market.json")
    if not market_path.exists():
        print("ERROR: data/market.json not found.")
        sys.exit(1)
    market = json.loads(market_path.read_text(encoding="utf-8"))
    stocks = market.get("stocks", {})
    themes = market.get("naver_themes", []) or []
    if not themes:
        print("  no themes in market.json")
        return
    print(f"  {len(themes)} themes loaded")

    # 1. 과거 history 로드 (모멘텀 시그널용)
    history_list = load_recent_history(days=30)
    print(f"  loaded {len(history_list)} historical snapshots")

    # 2. 테마 멤버 fetch (cache 활용, 주 1회 refresh)
    print("\n[Fetch] 테마 멤버 종목 (top 80)...")
    top_themes = themes[:80]  # 상위 80개 테마만
    theme_members_map = load_theme_members_cached(top_themes)
    print(f"  member data ready for {len(theme_members_map)} themes")

    # 3. DART 펀더 cache
    financials_cache = load_dart_financials()
    print(f"  DART financials: {len(financials_cache)} stocks")

    # 4. 외국인/기관 매매 데이터
    investor_top = market.get("investor_top", {})
    foreign_buy = []
    foreign_sell = []
    inst_buy = []
    inst_sell = []
    for mkt_key in ("KOSPI", "KOSDAQ", "all"):
        m = investor_top.get(mkt_key, {}) if isinstance(investor_top, dict) else {}
        if isinstance(m, dict):
            foreign_buy.extend((m.get("foreign", {}) or {}).get("buy", []) or [])
            foreign_sell.extend((m.get("foreign", {}) or {}).get("sell", []) or [])
            inst_buy.extend((m.get("institution", {}) or {}).get("buy", []) or [])
            inst_sell.extend((m.get("institution", {}) or {}).get("sell", []) or [])
    print(f"  investor data: foreign +{len(foreign_buy)}/-{len(foreign_sell)}, institution +{len(inst_buy)}/-{len(inst_sell)}")

    # 5. 가중치 로드 (backtest 후 auto-calibrated)
    weights = load_weights()
    print(f"  weights: M={weights['momentum']} F={weights['money_flow']} U={weights['fundamental']} C={weights['confidence']}"
          + (f" [calibrated, n={weights['_n_backtest_periods']}]" if weights.get("_calibrated") else " [default]"))

    # 6. 테마별 시그널 계산
    print("\n[Compute] 테마별 시그널...")
    theme_data = []
    for t in top_themes:
        name = t.get("name")
        no = t.get("no")
        if not name or not no:
            continue
        members = theme_members_map.get(no, [])
        if len(members) < 3:
            continue  # 멤버 너무 적으면 신뢰 어려움
        mom = compute_momentum_signals(history_list, name)
        if not mom:
            # 새로 등장한 테마 — history 없음. 모두 None으로 두면 percentile_score가 median(0.5) 부여
            mom = {"avg_rank_5d": None, "rank_delta_5d": None, "avg_change_5d": None, "top20_freq_30d": None}
        flow = compute_money_flow_signal(members, stocks,
                                         [foreign_buy, inst_buy], [foreign_sell, inst_sell])
        fund = compute_fundamental_signal(members, financials_cache)
        conf = compute_confidence(members, stocks)
        theme_data.append({
            "no": no,
            "name": name,
            "current_rank": themes.index(t) + 1,
            "current_change": t.get("change", 0),
            **mom, **flow, **fund, **conf,
        })

    # 7. 점수 정규화 + 결합
    print("[Score] 정규화 + 결합...")
    scores = normalize_scores(theme_data, weights)
    for td, sc in zip(theme_data, scores):
        td.update(sc)

    # 8. 정렬: total_score 높은 순
    theme_data.sort(key=lambda x: x["total_score"], reverse=True)

    # 8.5 top 30에 멤버 종목 (이름) 첨부 — frontend 클릭 시 표시용
    print("[멤버 종목 첨부] top 30 테마...")
    for td in theme_data[:30]:
        no = td.get("no")
        if not no:
            continue
        member_codes = theme_members_map.get(no, [])
        td["stocks"] = []
        for code in list(member_codes)[:30]:
            s = stocks.get(code)
            if s and s.get("name"):
                td["stocks"].append({
                    "code": code,
                    "name": s["name"],
                    "price": s.get("price", 0),
                    "change": s.get("change", 0),
                })

    # 9. 백테스트 정확도 로드 (있으면)
    stats_path = Path("data/theme_forecast_stats.json")
    backtest_stats = None
    if stats_path.exists():
        try:
            backtest_stats = json.loads(stats_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    # 10. 저장
    today = datetime.now(KST).strftime("%Y%m%d")
    payload = {
        "updated": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        "trading_day": today,
        "weights_used": weights,
        "backtest_stats": backtest_stats,  # 정확도 지표
        "history_days_available": len(history_list),
        "top_themes": theme_data[:30],
    }
    out_path = Path("data/theme_forecast.json")
    out_path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"\n✅ Saved theme_forecast.json (top {len(theme_data[:30])})")

    # 11. Archive 오늘의 forecast (백테스트용)
    archive_dir = Path("data/theme_forecast_history")
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / f"{today}.json"
    if not archive_path.exists():
        # 가벼운 버전: 핵심만
        archive_path.write_text(json.dumps({
            "trading_day": today,
            "themes": [
                {"name": t["name"], "no": t["no"], "total_score": t["total_score"], "rank_at_forecast": i + 1}
                for i, t in enumerate(theme_data[:30])
            ],
        }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(f"  archived to history: {archive_path.name}")

    # 인쇄: top 5
    print("\n--- TOP 5 ---")
    for i, t in enumerate(theme_data[:5]):
        print(f"  {i+1}. {t['name']} ({t['total_score']}) "
              f"M={t['momentum_score']} F={t['flow_score']} U={t['fundamental_score']} C={t['confidence_score']}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
