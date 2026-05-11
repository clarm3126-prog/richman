#!/usr/bin/env python3
"""스크리너 백테스트 — 과거 N일 전 strict/strong 통과 종목의 30/60일 후 수익률.

매주 1회 실행 (일요일).
- data/screener_results_history/{YYYYMMDD}.json 누적된 결과 활용 (없으면 비어있음)
- 또는 현재 결과를 매일 history에 저장하면서 점진적으로 백테스트 가능
- 1차: 단순 30일 후 종가 비교
- 통계: 승률, 평균 수익률, 최대 수익률, 최대 손실
- 카테고리별 집계: minervini_strict / minervini_strong / momentum_strong / pre_breakout

출력: data/backtest_stats.json
"""
import json
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path

import pytz

sys.path.insert(0, str(Path(__file__).parent))
from screener import fetch_all_stock_history

KST = pytz.timezone("Asia/Seoul")


def load_history_files(dir_name):
    """data/{dir_name}/{YYYYMMDD}.json 모두 로드. 날짜순."""
    d = Path(f"data/{dir_name}")
    if not d.exists():
        return []
    out = []
    for f in sorted(d.glob("*.json")):
        if f.stem == "index":
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            data["_date"] = f.stem
            out.append(data)
        except Exception:
            pass
    return out


def evaluate_picks(snapshots, category_filter, days_forward=30, max_picks_per_day=20):
    """과거 snapshot에서 카테고리 통과 종목 → days_forward 후 수익률.

    snapshots: list of {trading_day, results: [...]}
    category_filter: lambda r → bool
    """
    today = datetime.now(KST)
    # days_forward는 trading days로 계산하지만 cutoff는 calendar days로 변환 필요.
    # 252 trading days/year ≈ 365 calendar → ratio ~1.45. 안전마진 +5일 추가.
    calendar_cutoff_days = int(days_forward * 365 / 252) + 5
    cutoff = today - timedelta(days=calendar_cutoff_days)
    cutoff_str = cutoff.strftime("%Y%m%d")

    # cutoff_str보다 오래된 snapshot만 사용 (충분히 미래 가격 확보됨)
    eligible = [s for s in snapshots if s.get("_date", "") <= cutoff_str]
    if not eligible:
        return None

    # 모든 picks 수집 (날짜별 최대 max_picks_per_day개)
    all_picks = []  # [{date, code, entry_price, ...}]
    for snap in eligible:
        date = snap.get("_date") or snap.get("trading_day")
        results = snap.get("results", []) or []
        passed = [r for r in results if category_filter(r)][:max_picks_per_day]
        for r in passed:
            entry_price = r.get("price") or r.get("current_price")
            if not entry_price:
                continue
            all_picks.append({
                "date": date,
                "code": r["code"],
                "name": r.get("name", r["code"]),
                "entry_price": entry_price,
            })

    if not all_picks:
        return None

    # 수익률 계산: 각 pick의 entry → entry+days_forward 종가
    # 종가 fetch가 필요. 이 함수는 호출 측에서 미리 호출
    return all_picks


def fetch_exit_prices(picks, days_forward=30):
    """각 pick의 entry+N일 종가를 fetch. 한 번의 OHLC fetch로 여러 pick 처리."""
    codes = list({p["code"] for p in picks})
    print(f"  fetching exit prices for {len(codes)} unique stocks...")
    histories = fetch_all_stock_history(codes, days=120)  # 충분히 긴 윈도우
    return histories


def compute_returns(picks, histories, days_forward=30):
    """pick의 entry 날짜 + N일 종가로 수익률 계산."""
    returns = []
    for pick in picks:
        history = histories.get(pick["code"], [])
        if not history:
            continue
        # entry 날짜 기준 +days_forward 트레이딩 일 후 종가
        entry_date = pick["date"]
        # entry_date 인덱스 찾기
        entry_idx = None
        for i, h in enumerate(history):
            h_date = (h.get("date") or "").replace("-", "")
            if h_date == entry_date:
                entry_idx = i
                break
        if entry_idx is None:
            continue
        # +N 거래일 (대략 N일 ≈ N * 252/365 ≈ N*0.7 거래일이지만 N일 = N 거래일로 단순화)
        target_idx = entry_idx + days_forward
        if target_idx >= len(history):
            continue
        exit_price = history[target_idx]["close"]
        if exit_price > 0 and pick["entry_price"] > 0:
            ret = (exit_price - pick["entry_price"]) / pick["entry_price"] * 100
            returns.append({
                "code": pick["code"],
                "name": pick["name"],
                "date": entry_date,
                "entry": pick["entry_price"],
                "exit": exit_price,
                "return_pct": round(ret, 2),
            })
    return returns


def stats_summary(returns):
    if not returns:
        return None
    rs = [r["return_pct"] for r in returns]
    n = len(rs)
    win_n = sum(1 for r in rs if r > 0)
    big_win_n = sum(1 for r in rs if r >= 20)
    big_loss_n = sum(1 for r in rs if r <= -10)
    return {
        "count": n,
        "win_rate": round(win_n / n * 100, 1),
        "avg_return": round(sum(rs) / n, 2),
        "median_return": round(sorted(rs)[n // 2], 2),
        "max_return": round(max(rs), 2),
        "min_return": round(min(rs), 2),
        "big_wins_20pct": big_win_n,
        "big_losses_neg10pct": big_loss_n,
        "best_picks": sorted(returns, key=lambda x: x["return_pct"], reverse=True)[:5],
        "worst_picks": sorted(returns, key=lambda x: x["return_pct"])[:5],
    }


def save_current_screener_to_history():
    """오늘의 screener_results.json + momentum_results.json을
    data/screener_results_history/{day}.json + momentum_results_history/{day}.json 으로 누적.
    """
    today = datetime.now(KST).strftime("%Y%m%d")
    saved = []
    for src, dst_dir in [
        ("data/screener_results.json", "data/screener_results_history"),
        ("data/momentum_results.json", "data/momentum_results_history"),
    ]:
        src_path = Path(src)
        if not src_path.exists():
            continue
        try:
            data = json.loads(src_path.read_text(encoding="utf-8"))
            day = data.get("trading_day", today)
            d = Path(dst_dir)
            d.mkdir(parents=True, exist_ok=True)
            dst_path = d / f"{day}.json"
            if not dst_path.exists():
                # 가벼운 버전: 점수 + 핵심 필드만
                lite = {
                    "trading_day": day,
                    "results": [
                        {
                            "code": r["code"],
                            "name": r.get("name"),
                            "price": r.get("price") or r.get("current_price"),
                            "minervini_strict": r.get("minervini_strict", False),
                            "minervini_strong": r.get("minervini_strong", False),
                            "momentum_strong": r.get("momentum_strong", False),
                            "pre_breakout": r.get("pre_breakout", False),
                            "total_score": r.get("total_score"),
                        }
                        for r in (data.get("results") or [])[:50]
                    ],
                }
                dst_path.write_text(json.dumps(lite, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
                saved.append(str(dst_path))
        except Exception as e:
            print(f"  history save failed: {src}: {e}")
    if saved:
        print(f"  archived to history: {saved}")


def main():
    print(f"=== Backtest — {datetime.now(KST).strftime('%Y-%m-%d %H:%M KST')} ===")

    # 1. 오늘 결과를 history에 archive
    save_current_screener_to_history()

    # 2. snapshot 로드
    minervini_snaps = load_history_files("screener_results_history")
    momentum_snaps = load_history_files("momentum_results_history")
    print(f"  minervini snapshots: {len(minervini_snaps)}")
    print(f"  momentum snapshots: {len(momentum_snaps)}")

    if not minervini_snaps and not momentum_snaps:
        print("  no historical snapshots yet — run daily for ~30 days to build")
        # 빈 결과 저장 (frontend가 적절히 표시)
        out_path = Path("data/backtest_stats.json")
        out_path.write_text(json.dumps({
            "updated": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
            "status": "no_history",
            "message": "백테스트 데이터 누적 중... 30일 이상 지나면 통계 표시",
            "categories": {},
        }, ensure_ascii=False), encoding="utf-8")
        return

    # 3. 카테고리별 평가
    categories = {
        "minervini_strict": (minervini_snaps, lambda r: r.get("minervini_strict")),
        "minervini_strong": (minervini_snaps, lambda r: r.get("minervini_strong")),
        "momentum_strong": (momentum_snaps, lambda r: r.get("momentum_strong")),
        "pre_breakout": (momentum_snaps, lambda r: r.get("pre_breakout")),
    }

    # 모든 카테고리에서 picks 모음 → 한 번에 OHLC fetch
    all_picks_by_cat = {}
    all_codes = set()
    for cat, (snaps, filt) in categories.items():
        picks_30 = evaluate_picks(snaps, filt, days_forward=30)
        picks_60 = evaluate_picks(snaps, filt, days_forward=60)
        if picks_30:
            all_codes.update(p["code"] for p in picks_30)
        if picks_60:
            all_codes.update(p["code"] for p in picks_60)
        all_picks_by_cat[cat] = (picks_30 or [], picks_60 or [])

    if not all_codes:
        print("  not enough historical data with sufficient time to evaluate")
        out_path = Path("data/backtest_stats.json")
        out_path.write_text(json.dumps({
            "updated": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
            "status": "warming_up",
            "message": "30일 이상 지난 snapshot이 부족합니다. 계속 누적 중...",
            "categories": {},
        }, ensure_ascii=False), encoding="utf-8")
        return

    print(f"\n[OHLC] fetching prices for {len(all_codes)} unique codes...")
    histories = fetch_all_stock_history(list(all_codes), days=120)

    # 4. 카테고리별 수익률 계산 + 통계
    cat_results = {}
    for cat, (picks_30, picks_60) in all_picks_by_cat.items():
        ret_30 = compute_returns(picks_30, histories, days_forward=30) if picks_30 else []
        ret_60 = compute_returns(picks_60, histories, days_forward=60) if picks_60 else []
        cat_results[cat] = {
            "30d": stats_summary(ret_30),
            "60d": stats_summary(ret_60),
        }
        if ret_30:
            s = cat_results[cat]["30d"]
            print(f"  [{cat}] 30d: n={s['count']} win_rate={s['win_rate']}% avg={s['avg_return']}%")

    # 5. 저장
    out_path = Path("data/backtest_stats.json")
    out_path.write_text(json.dumps({
        "updated": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        "status": "ok",
        "snapshot_counts": {
            "minervini": len(minervini_snaps),
            "momentum": len(momentum_snaps),
        },
        "categories": cat_results,
    }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"\n✅ Saved backtest_stats.json")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
