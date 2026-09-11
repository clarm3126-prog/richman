#!/usr/bin/env python3
"""스크리너 백테스트 — 과거에 조건을 통과한 종목을 그냥 들고 있었으면 어땠나.

- data/screener_results_history/{YYYYMMDD}.json 누적된 결과 활용 (없으면 비어있음)
- 보유 기간별로 따로 낸다 (HORIZONS). 짧게 보면 손실인데 길게 보면 다른 경우가
  있어서, 기간 하나만 보고 기법을 판단하면 안 된다.
- 통계: 승률, 평균 수익률, 최대 수익률, 최대 손실
- 카테고리별 집계: minervini_strict / minervini_strong / momentum_strong / pre_breakout

**손절선도 트레일링도 걸지 않은 숫자다.** 조건에 걸린 날 사서 N거래일 뒤
종가에 판 것뿐이다. 이 단서를 빼고 인용하면 기법을 잘못 평가하게 된다.

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

# 보유 기간(거래일). 여기만 고치면 전체가 따라온다.
#
# 긴 기간일수록 그만큼 오래된 snapshot이 있어야 값이 나온다. 120일짜리는
# 약 6개월(178일) 이상 지난 snapshot이 필요해서, 기록을 그만큼 모으기 전에는
# null로 남는다. 버그가 아니라 아직 채워지지 않은 것이고,
# horizon_ready에 언제부터 값이 나오는지 적어둔다.
HORIZONS = [30, 60, 120]

# 종목 하나당 받아오는 일봉 길이.
# 산 날짜가 이 창 안에 있어야 그 뒤 종가를 찾을 수 있으므로,
# 가장 긴 보유 기간보다 넉넉해야 한다.
HISTORY_DAYS = max(252, max(HORIZONS) + 130)


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


def calendar_days_for(days_forward):
    """거래일 N일을 달력 날짜로 바꾼다.

    1년에 거래일이 약 252일, 달력은 365일이라 1.45배쯤 된다. 휴장이 몰린
    구간을 감안해 5일을 더 얹는다. snapshot이 이만큼 오래돼야 그 뒤
    N거래일 종가가 실제로 존재한다.
    """
    return int(days_forward * 365 / 252) + 5


def evaluate_picks(snapshots, category_filter, days_forward=30, max_picks_per_day=20):
    """과거 snapshot에서 카테고리 통과 종목 → days_forward 후 수익률.

    snapshots: list of {trading_day, results: [...]}
    category_filter: lambda r → bool
    """
    today = datetime.now(KST)
    cutoff = today - timedelta(days=calendar_days_for(days_forward))
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


def horizon_readiness(snapshots):
    """보유 기간별로 값이 나올 준비가 됐는지, 아니면 언제부터 나오는지.

    긴 기간이 null인 게 고장인지 아직 덜 모은 건지 화면에서 구분하려고 낸다.
    이게 없으면 사람이 "왜 비어 있지" 하고 코드를 뒤지게 된다.
    """
    today = datetime.now(KST)
    ages = []
    oldest = None
    for snap in snapshots:
        d = snap.get("_date") or ""
        try:
            dt = KST.localize(datetime.strptime(d, "%Y%m%d"))
        except ValueError:
            continue
        ages.append((today - dt).days)
        if oldest is None or dt < oldest:
            oldest = dt

    out = {}
    for h in HORIZONS:
        need = calendar_days_for(h)
        usable = sum(1 for a in ages if a >= need)
        info = {
            "needs_snapshot_older_than_days": need,
            "snapshots_usable": usable,
            "ready": usable > 0,
        }
        if usable == 0 and oldest is not None:
            # 가장 오래된 기록이 need일을 채우는 날. 그날부터 값이 나온다.
            info["first_value_on"] = (oldest + timedelta(days=need)).strftime("%Y-%m-%d")
        out[f"{h}d"] = info
    return out


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
                # 전체 상세 (기술 신호/펀더멘털/setup 등 모두 포함) — top 100, 과거 조회용
                archive = {
                    "trading_day": day,
                    "results": (data.get("results") or [])[:100],
                }
                dst_path.write_text(json.dumps(archive, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
                saved.append(str(dst_path))
            # index.json 갱신 — 사용 가능 날짜 목록 (frontend 날짜 선택용)
            dates = sorted(
                [f.stem for f in d.glob("*.json") if f.stem != "index"],
                reverse=True,
            )
            (d / "index.json").write_text(
                json.dumps({"dates": dates}, separators=(",", ":")), encoding="utf-8")
        except Exception as e:
            print(f"  history save failed: {src}: {e}")

    # 오닐 ATH 돌파 archive — 구조가 달라서 별도 처리 (frontend 날짜 선택용)
    ath_src = Path("data/ath_breakouts.json")
    if ath_src.exists():
        try:
            data = json.loads(ath_src.read_text(encoding="utf-8"))
            day = data.get("trading_day", today)
            d = Path("data/ath_breakouts_history")
            d.mkdir(parents=True, exist_ok=True)
            dst_path = d / f"{day}.json"
            # 돌파 종목이 하나라도 있을 때만 archive (빈 날은 캘린더에서 제외)
            if (data.get("close") or data.get("intraday")) and not dst_path.exists():
                lite = {
                    "trading_day": day,
                    "updated": data.get("updated", ""),
                    "intraday": data.get("intraday", []),
                    "close": data.get("close", []),
                }
                dst_path.write_text(json.dumps(lite, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
                saved.append(str(dst_path))
            dates = sorted(
                [f.stem for f in d.glob("*.json") if f.stem != "index"],
                reverse=True,
            )
            (d / "index.json").write_text(
                json.dumps({"dates": dates}, separators=(",", ":")), encoding="utf-8")
        except Exception as e:
            print(f"  ath history save failed: {e}")

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
        by_horizon = {}
        for h in HORIZONS:
            picks = evaluate_picks(snaps, filt, days_forward=h) or []
            all_codes.update(p["code"] for p in picks)
            by_horizon[h] = picks
        all_picks_by_cat[cat] = by_horizon

    if not all_codes:
        print("  not enough historical data with sufficient time to evaluate")
        out_path = Path("data/backtest_stats.json")
        out_path.write_text(json.dumps({
            "updated": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
            "status": "warming_up",
            "message": f"{min(HORIZONS)}일 이상 지난 snapshot이 부족합니다. 계속 누적 중...",
            "categories": {},
        }, ensure_ascii=False), encoding="utf-8")
        return

    print(f"\n[OHLC] fetching prices for {len(all_codes)} unique codes...")
    histories = fetch_all_stock_history(list(all_codes), days=HISTORY_DAYS)

    # 4. 카테고리별 · 보유 기간별 수익률 계산 + 통계
    cat_results = {}
    for cat, by_horizon in all_picks_by_cat.items():
        stats = {}
        for h in HORIZONS:
            picks = by_horizon.get(h) or []
            rets = compute_returns(picks, histories, days_forward=h) if picks else []
            stats[f"{h}d"] = stats_summary(rets)
        cat_results[cat] = stats
        for h in HORIZONS:
            st = stats[f"{h}d"]
            if st:
                print(f"  [{cat}] {h}d: n={st['count']} "
                      f"win_rate={st['win_rate']}% avg={st['avg_return']}%")
            else:
                print(f"  [{cat}] {h}d: 아직 데이터 없음")

    # 5. 저장
    out_path = Path("data/backtest_stats.json")
    out_path.write_text(json.dumps({
        "updated": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        "status": "ok",
        "snapshot_counts": {
            "minervini": len(minervini_snaps),
            "momentum": len(momentum_snaps),
        },
        "horizons": HORIZONS,
        "horizon_ready": horizon_readiness(minervini_snaps + momentum_snaps),
        "categories": cat_results,
    }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"\n✅ Saved backtest_stats.json")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
