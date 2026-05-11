#!/usr/bin/env python3
"""테마 예측 자체 검증 — 주 1회 실행.

매주 토요일 실행:
1. data/theme_forecast_history/ 모든 archive 로드
2. 각 archive의 7일/14일/30일 후 실제 ranking과 비교
3. TOP 5 / TOP 10 적중률, 평균 ranking 변화, 등락률 계산
4. 시그널별 예측력 평가 → weights 자동 조정 (Phase 2)
5. data/theme_forecast_stats.json 갱신

신뢰도 점진적 향상:
- 데이터 < 4주: status="warming_up", 가중치 default
- 데이터 4~12주: 정확도 표시, 가중치 default 유지 (sample 부족)
- 데이터 12주+: 정확도 + signal-by-signal 분석 → 가중치 자동 조정
"""
import json
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path

import pytz

KST = pytz.timezone("Asia/Seoul")


def load_forecast_archives():
    """data/theme_forecast_history/{YYYYMMDD}.json 모두 로드."""
    d = Path("data/theme_forecast_history")
    if not d.exists():
        return []
    out = []
    for f in sorted(d.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            data["_date"] = f.stem
            out.append(data)
        except Exception:
            pass
    return out


def load_actual_history():
    """data/history/{date}.json — 실제 ranking 진실 데이터."""
    d = Path("data/history")
    if not d.exists():
        return {}
    out = {}
    for f in d.glob("*.json"):
        if f.stem == "index":
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            out[f.stem] = data
        except Exception:
            pass
    return out


def find_actual_rank(actual_history, date_str, theme_name):
    """특정 날짜의 실제 ranking에서 theme 찾기."""
    snap = actual_history.get(date_str)
    if not snap:
        return None
    for i, t in enumerate(snap.get("themes", []) or []):
        if t.get("name") == theme_name:
            return i + 1
    return None


def find_avg_change(actual_history, dates, theme_name):
    """기간 평균 등락률."""
    changes = []
    for d in dates:
        snap = actual_history.get(d)
        if not snap:
            continue
        for t in snap.get("themes", []) or []:
            if t.get("name") == theme_name:
                changes.append(t.get("change", 0))
                break
    return sum(changes) / len(changes) if changes else None


def date_range_after(start_date_str, days):
    """start + 1 ~ start + days의 YYYYMMDD list."""
    start = datetime.strptime(start_date_str, "%Y%m%d").replace(tzinfo=KST)
    return [(start + timedelta(days=i)).strftime("%Y%m%d") for i in range(1, days + 1)]


def evaluate_forecast_period(forecast, actual_history, days_forward, top_n=5):
    """단일 forecast의 N일 후 적중률 + 평균 등락률.
    Returns: {hit_count, top_n, avg_actual_rank, avg_change, success_count}
    """
    forecast_themes = forecast.get("themes", [])[:top_n]
    if not forecast_themes:
        return None
    forecast_date = forecast.get("trading_day", forecast.get("_date"))
    if not forecast_date:
        return None
    dates_after = date_range_after(forecast_date, days_forward)
    # 마지막 날짜의 ranking과 비교
    last_date = dates_after[-1]
    actual_top_set = set()
    last_snap = actual_history.get(last_date)
    if not last_snap:
        # 마지막 날짜 데이터 없으면 가장 가까운 이전 날짜 사용
        for d in reversed(dates_after):
            if d in actual_history:
                last_snap = actual_history[d]
                break
    if not last_snap:
        return None
    for i, t in enumerate(last_snap.get("themes", []) or []):
        if i < 20:  # actual TOP 20에 들어가면 hit
            actual_top_set.add(t.get("name"))

    hit = 0
    total_changes = []
    for f in forecast_themes:
        name = f["name"]
        if name in actual_top_set:
            hit += 1
        avg_chg = find_avg_change(actual_history, dates_after, name)
        if avg_chg is not None:
            total_changes.append(avg_chg)

    return {
        "hit_count": hit,
        "top_n": top_n,
        "hit_rate": round(hit / top_n * 100, 1),
        "avg_change_after": round(sum(total_changes) / len(total_changes), 2) if total_changes else None,
        "evaluated_themes": len(total_changes),
    }


def aggregate_stats(evaluations, days_forward):
    """여러 evaluation을 평균."""
    valid = [e for e in evaluations if e]
    if not valid:
        return None
    avg_hit_rate = sum(e["hit_rate"] for e in valid) / len(valid)
    avg_changes = [e["avg_change_after"] for e in valid if e.get("avg_change_after") is not None]
    avg_change_after = sum(avg_changes) / len(avg_changes) if avg_changes else None
    return {
        "n_periods": len(valid),
        "days_forward": days_forward,
        "avg_hit_rate": round(avg_hit_rate, 1),
        "avg_change_after": round(avg_change_after, 2) if avg_change_after is not None else None,
    }


def calibrate_weights(forecasts, actual_history):
    """signal별 예측력 평가 → weights 조정.
    각 signal의 high group vs low group 30일 평균 등락률 차이 → 예측력.

    Phase 2 활성: backtest periods >= 12 (3개월)
    """
    # 단순화: 현재는 default 반환. 나중에 활성화.
    return None


def main():
    print(f"=== Theme Forecast Backtest — {datetime.now(KST).strftime('%Y-%m-%d %H:%M KST')} ===")

    archives = load_forecast_archives()
    actual_history = load_actual_history()
    print(f"  forecast archives: {len(archives)}")
    print(f"  actual history snapshots: {len(actual_history)}")

    # 7일 backtest는 7일 이상 지난 archive만
    today_str = datetime.now(KST).strftime("%Y%m%d")
    cutoff_7d = (datetime.now(KST) - timedelta(days=7)).strftime("%Y%m%d")
    cutoff_14d = (datetime.now(KST) - timedelta(days=14)).strftime("%Y%m%d")
    cutoff_30d = (datetime.now(KST) - timedelta(days=30)).strftime("%Y%m%d")

    eligible_7d = [a for a in archives if a.get("_date", "") <= cutoff_7d]
    eligible_14d = [a for a in archives if a.get("_date", "") <= cutoff_14d]
    eligible_30d = [a for a in archives if a.get("_date", "") <= cutoff_30d]
    print(f"  eligible: 7d={len(eligible_7d)}, 14d={len(eligible_14d)}, 30d={len(eligible_30d)}")

    if len(eligible_7d) < 3:
        print("  not enough archives — saving warming_up status")
        out = {
            "updated": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
            "status": "warming_up",
            "message": f"백테스트 데이터 누적 중 ({len(archives)}/7일치 필요)",
            "n_archives": len(archives),
        }
        Path("data/theme_forecast_stats.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        return

    # 7d / 14d / 30d 적중률 평가
    eval_7d_top5 = [evaluate_forecast_period(a, actual_history, 7, top_n=5) for a in eligible_7d[-30:]]
    eval_7d_top10 = [evaluate_forecast_period(a, actual_history, 7, top_n=10) for a in eligible_7d[-30:]]
    eval_14d_top5 = [evaluate_forecast_period(a, actual_history, 14, top_n=5) for a in eligible_14d[-30:]] if eligible_14d else []
    eval_30d_top5 = [evaluate_forecast_period(a, actual_history, 30, top_n=5) for a in eligible_30d[-30:]] if eligible_30d else []

    stats = {
        "updated": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        "status": "ok",
        "n_archives": len(archives),
        "n_eligible_7d": len(eligible_7d),
        "n_eligible_14d": len(eligible_14d),
        "n_eligible_30d": len(eligible_30d),
        "stats_7d_top5": aggregate_stats(eval_7d_top5, 7),
        "stats_7d_top10": aggregate_stats(eval_7d_top10, 7),
        "stats_14d_top5": aggregate_stats(eval_14d_top5, 14),
        "stats_30d_top5": aggregate_stats(eval_30d_top5, 30),
    }

    # weights 자동 조정 (12주+ 데이터 있을 때만)
    if len(eligible_30d) >= 12:
        new_weights = calibrate_weights(eligible_30d, actual_history)
        if new_weights:
            new_weights["_calibrated"] = True
            new_weights["_n_backtest_periods"] = len(eligible_30d)
            Path("data/theme_forecast_weights.json").write_text(
                json.dumps(new_weights, ensure_ascii=False, indent=2), encoding="utf-8")
            stats["weights_calibrated"] = True
            print(f"  ✅ calibrated weights from {len(eligible_30d)} periods")

    Path("data/theme_forecast_stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✅ Saved theme_forecast_stats.json")

    # 출력
    if stats["stats_7d_top5"]:
        print(f"\n--- TOP 5 / 7일 후 ---")
        s = stats["stats_7d_top5"]
        print(f"  적중률 (TOP 20 잔류): {s['avg_hit_rate']}%")
        print(f"  평균 등락률: {s.get('avg_change_after', '?')}%")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
