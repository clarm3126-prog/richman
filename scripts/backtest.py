#!/usr/bin/env python3
"""스크리너 백테스트 — 과거에 조건을 통과한 종목을 그냥 들고 있었으면 어땠나.

- data/screener_results_history/{YYYYMMDD}.json 누적된 결과 활용 (없으면 비어있음)
- 보유 기간별로 따로 낸다 (HORIZONS). 짧게 보면 손실인데 길게 보면 다른 경우가
  있어서, 기간 하나만 보고 기법을 판단하면 안 된다.
- 통계: 승률, 평균 수익률, 최대 수익률, 최대 손실
- 카테고리별 집계: minervini_strict / minervini_strong / momentum_strong / pre_breakout

**손절선도 트레일링도 걸지 않은 숫자다.** 조건에 걸린 날 종가에 사서
N거래일 뒤 종가에 판 것뿐이다. 이 단서를 빼고 인용하면 기법을 잘못 평가하게 된다.

매수가·매도가 모두 수정주가 일봉에서 읽는다. 이유는 compute_returns()에.

승률과 평균만으로는 이 숫자가 기법 탓인지 장 탓인지 가릴 수 없어서,
읽는 데 필요한 세 가지를 같이 낸다.

  period   표본이 언제 것이고 그때 지수가 어땠나 (최대낙폭 포함)
           → index_window(). 이게 없으면 "이 기법은 승률 20%"로 인용된다.
  by_date  하루를 관측 하나로 센 승률. 같은 종목이 며칠씩 걸려 있어
           픽 단위로 세면 같은 베팅이 반복 계산되는데, 빠지는 구간일수록
           오래 걸려 있으므로 표본이 그쪽으로 기운다.
  rules.benchmark_matched
           손절로 실제로 들고 있던 날수만큼의 지수. 규칙을 지키면 평균
           7일 만에 빠져나오므로, 그 수익률을 30일짜리 지수와 견주면
           비교가 성립하지 않는다. 같은 날수끼리만 견줄 수 있다.

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
            # snapshot에 적힌 값은 그날의 원주가다. 매수가로 쓰면 매도가(수정주가)와
            # 기준이 어긋나므로 compute_returns()에서 대조용으로만 본다.
            snapshot_price = r.get("price") or r.get("current_price")
            if not snapshot_price:
                continue
            all_picks.append({
                "date": date,
                "code": r["code"],
                "name": r.get("name", r["code"]),
                "snapshot_price": snapshot_price,
            })

    if not all_picks:
        return None

    # 수익률 계산: 각 pick의 entry → entry+days_forward 종가
    # 종가 fetch가 필요. 이 함수는 호출 측에서 미리 호출
    return all_picks


# 도구가 안내하는 매도 규칙. index.html의 안내 문구, exit_signals.py의
# 실제 알림과 같은 값이어야 한다. 한쪽만 바뀌면 "화면과 다르다"가 된다.
STOP_PCT = -7.0
TRAIL_STEPS = [(50.0, 50), (20.0, 21)]  # (이익률, 이탈을 볼 이동평균) — 높은 쪽 먼저


def _sma_at(closes, i, period):
    """i번째 날까지의 period일 단순이동평균."""
    if i + 1 < period:
        return None
    return sum(closes[i + 1 - period:i + 1]) / period


def apply_rules(history, entry_idx, target_idx):
    """-7% 손절과 이익 구간 트레일링을 적용했을 때의 수익률.

    기존 숫자는 조건에 걸린 날 사서 기간이 끝날 때까지 그냥 들고 있은
    결과다. 정작 도구는 -7%에 자르고 이익이 나면 이동평균을 따라 올리라고
    안내한다. 안내대로 했을 때의 숫자가 없으면, 도구를 쓰는 사람이 실제로
    겪을 결과를 아무도 모르는 셈이다.

    손절가는 -7% 정확히가 아니라 **그날 종가**로 잡는다. 갭 하락으로 한 번에
    더 빠지는 날이 있어서, -7%로 적으면 실제보다 좋게 나온다.

    돌려주는 값: (수익률, 며칠 만에, 왜 팔았는지)
    """
    closes = [h["close"] for h in history]
    entry = closes[entry_idx]
    if entry <= 0:
        return None
    peak = 0.0
    for i in range(entry_idx + 1, target_idx + 1):
        c = closes[i]
        if c <= 0:
            continue
        gain = (c - entry) / entry * 100
        if gain <= STOP_PCT:
            return gain, i - entry_idx, "손절"
        peak = max(peak, gain)
        for need, period in TRAIL_STEPS:
            if peak >= need:
                ma = _sma_at(closes, i, period)
                if ma and c < ma:
                    return gain, i - entry_idx, f"MA{period} 이탈"
                break
    return (closes[target_idx] - entry) / entry * 100, target_idx - entry_idx, "기간 만료"


def benchmark_return(index_closes, entry_date, days_forward):
    """같은 날 사서 같은 기간 지수를 들고 있었을 때.

    승률 20%가 낮아 보이는지 아닌지는 같은 기간 시장이 어땠는지를 알아야
    말할 수 있다. 이 줄이 없으면 숫자가 혼자 떠서 오해를 부른다.
    """
    dates = sorted(index_closes)
    try:
        i = dates.index(entry_date)
    except ValueError:
        return None
    j = i + days_forward
    if j >= len(dates):
        return None
    a, b = index_closes[dates[i]], index_closes[dates[j]]
    if a <= 0:
        return None
    return (b / a - 1) * 100


def index_window(index_closes, first_entry, last_entry, days_forward):
    """이 통계가 실제로 덮는 구간에서 지수가 어땠나.

    첫 매수일부터 '마지막 매수일 + 보유기간'까지가 이 숫자들이 실제로 겪은
    구간이다. 그 구간에서 지수가 어디까지 올랐다 어디까지 빠졌는지를 같이
    적지 않으면, 평균 -15%가 기법 탓인지 장 탓인지 읽는 사람이 가릴 수 없다.

    최대낙폭을 굳이 같이 내는 이유가 있다. 시작과 끝만 적으면 조용한 장처럼
    보이는 구간에도 중간에 반토막이 들어 있을 수 있다. 조건에 걸린 종목은
    대개 지수보다 더 크게 움직이므로, 그 구간에 낙폭이 있었다는 사실 하나가
    승률이 왜 그 모양인지를 거의 다 설명한다.
    """
    if not index_closes:
        return None
    dates = sorted(index_closes)
    i = next((k for k, d in enumerate(dates) if d >= first_entry), None)
    j = next((k for k in range(len(dates) - 1, -1, -1) if dates[k] <= last_entry), None)
    if i is None or j is None or j < i:
        return None
    # 마지막 매수분이 팔릴 때까지 포함한다. 지수 기록이 거기까지 없으면
    # 있는 데까지만 본다.
    j = min(j + days_forward, len(dates) - 1)
    span = [index_closes[d] for d in dates[i:j + 1] if index_closes[d] > 0]
    if len(span) < 2:
        return None
    peak = span[0]
    mdd = 0.0
    for v in span:
        peak = max(peak, v)
        mdd = min(mdd, (v / peak - 1) * 100)
    return {
        "from": dates[i],
        "to": dates[j],
        "change_pct": round((span[-1] / span[0] - 1) * 100, 1),
        "high": round(max(span), 2),
        "low": round(min(span), 2),
        "max_drawdown_pct": round(mdd, 1),
    }


def compute_returns(picks, histories, days_forward=30, index_closes=None):
    """pick의 entry 날짜 + N일 종가로 수익률 계산.

    매수가도 매도가와 **같은 일봉 시리즈**에서 읽는다. 네이버 일봉은 수정주가라
    증자·분할이 있으면 그 이전 구간이 소급해서 조정된다. 반면 snapshot에는
    그날 거래되던 원주가가 그대로 적혀 있다. 둘을 섞으면 권리락이 한쪽에만
    반영돼서, 주식 수가 늘어난 것이 그대로 손실로 찍힌다.
    (티엘비 356860: 2026-07 1:1 무상증자 → 30일 수익률이 -78.9%로 기록됐다.)
    같은 시리즈에서 읽으면 조정 계수가 분자·분모에서 상쇄되므로, 나중에 또
    증자가 나와서 과거 구간이 다시 조정돼도 수익률은 그대로 남는다.
    """
    returns = []
    adjusted_n = 0
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
        # 일봉에 그날이 없으면(상장폐지·거래정지·fetch 실패) 이 pick은 버린다.
        # snapshot 가격으로 때우면 기준이 다른 두 값을 다시 섞는 셈이라,
        # 표본 몇 개를 잃더라도 빼는 쪽이 맞다. 매도가가 없어 어차피 빠지던
        # 픽들이라 표본 수는 고치기 전과 같다.
        if entry_idx is None:
            continue
        # +N 거래일 (대략 N일 ≈ N * 252/365 ≈ N*0.7 거래일이지만 N일 = N 거래일로 단순화)
        target_idx = entry_idx + days_forward
        if target_idx >= len(history):
            continue
        entry_price = history[entry_idx]["close"]
        exit_price = history[target_idx]["close"]
        if exit_price > 0 and entry_price > 0:
            snap = pick.get("snapshot_price") or 0
            if snap and abs(entry_price / snap - 1) > 0.02:
                adjusted_n += 1
            ret = (exit_price - entry_price) / entry_price * 100
            row = {
                "code": pick["code"],
                "name": pick["name"],
                "date": entry_date,
                "entry": entry_price,
                "exit": exit_price,
                "return_pct": round(ret, 2),
            }
            ruled = apply_rules(history, entry_idx, target_idx)
            if ruled:
                row["ruled_pct"] = round(ruled[0], 2)
                row["ruled_days"] = ruled[1]
                row["ruled_reason"] = ruled[2]
            if index_closes:
                b = benchmark_return(index_closes, entry_date, days_forward)
                if b is not None:
                    row["bench_pct"] = round(b, 2)
                # 규칙을 지키면 평균 7일 만에 손절로 빠져나온다. 그렇게 나온
                # 수익률을 30일 내내 들고 있은 지수와 나란히 적으면 종목을
                # 잘 골랐던 것처럼 보이지만, 실제로는 대부분 현금으로 비켜서
                # 있던 덕이다. 들고 있던 날수만큼의 지수도 같이 재둔다.
                # 두 숫자가 하는 말이 다르다:
                #   bench_pct         — 지수를 사서 기간 내내 들고 있었을 때
                #   bench_matched_pct — 내가 실제로 물려 있던 날 동안의 지수
                if ruled:
                    bm = benchmark_return(index_closes, entry_date, ruled[1])
                    if bm is not None:
                        row["bench_matched_pct"] = round(bm, 2)
            returns.append(row)
    if adjusted_n:
        # 수정주가로 조정된 픽이 몇 개인지 남긴다. 이 줄이 0으로 바뀌면
        # 가격 소스가 원주가로 바뀐 것이므로 이 함수의 전제를 다시 봐야 한다.
        print(f"    ({adjusted_n}/{len(returns)} picks: snapshot 원주가와 수정주가가 2% 넘게 다름)")
    return returns


# 승률을 숫자로 말해도 되는 최소 표본.
#
# 픽 수로 재지 않는다. 같은 종목이 며칠씩 계속 걸려 있어서 픽 수는
# 얼마든지 부풀려지기 때문이다. 실제로 서로 다른 베팅이 몇 번이었는지를
# 재려면 종목 수와 매수일 수를 봐야 한다.
#
# pre_breakout이 이 가드를 만든 이유다. 종목 9개 · 20건으로 승률 75%가
# 찍혔는데, 이 숫자는 인용하기 딱 좋게 생겼고 근거는 거의 없다.
MIN_SAMPLE_CODES = 30   # 서로 다른 종목 수
MIN_SAMPLE_DAYS = 10    # 서로 다른 매수일 수


def stats_summary(returns, index_closes=None, days_forward=None):
    if not returns:
        return None
    rs = [r["return_pct"] for r in returns]
    n = len(rs)
    win_n = sum(1 for r in rs if r > 0)
    big_win_n = sum(1 for r in rs if r >= 20)
    big_loss_n = sum(1 for r in rs if r <= -10)
    out = {
        "count": n,
        # 픽 수는 같은 종목이 여러 날 다시 잡힌 것까지 센 값이다. 조건에
        # 걸린 종목은 며칠씩 계속 걸려 있으므로, n을 독립 시행 수로 읽으면
        # 표본이 실제보다 훨씬 많아 보인다. 종목 수를 같이 낸다.
        "unique_codes": len({r["code"] for r in returns}),
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

    # 언제 산 것들인가. 이 줄이 없으면 숫자만 남아서, 표본이 어떤 장이었는지
    # 모른 채 "이 기법은 승률 20%"로 인용된다.
    dates = sorted({r["date"] for r in returns})
    out["period"] = {
        "first_entry": dates[0],
        "last_entry": dates[-1],
        "entry_days": len(dates),
    }
    if index_closes and days_forward:
        w = index_window(index_closes, dates[0], dates[-1], days_forward)
        if w:
            out["period"]["index"] = w

    # 하루를 관측 하나로 센 값.
    #
    # 조건에 걸린 종목은 며칠씩 계속 걸려 있어서, 픽 단위로 세면 같은 베팅이
    # 걸려 있던 날수만큼 반복 계산된다. 게다가 빠지는 구간일수록 오래 걸려
    # 있으므로 표본이 그쪽으로 기운다. 날짜별로 묶어 평균을 내면 그 치우침이
    # 사라진다. 두 승률이 크게 어긋나면 픽 단위 쪽을 믿으면 안 된다는 뜻이다.
    by_day = {}
    for r in returns:
        by_day.setdefault(r["date"], []).append(r["return_pct"])
    daily = [sum(v) / len(v) for v in by_day.values()]
    dn = len(daily)
    out["by_date"] = {
        "days": dn,
        "win_rate": round(sum(1 for x in daily if x > 0) / dn * 100, 1),
        "avg_return": round(sum(daily) / dn, 2),
        "median_return": round(sorted(daily)[dn // 2], 2),
    }

    # 이 표본으로 승률을 말해도 되는지. 화면과 대본 스크립트가 이 플래그
    # 하나만 보게 해서, 기준이 두 군데로 갈라지지 않게 한다.
    # 통계 자체는 그대로 남겨둔다 — 숨기는 건 보여주는 쪽이지 계산이 아니다.
    out["sample"] = {
        "codes": out["unique_codes"],
        "days": dn,
        "min_codes": MIN_SAMPLE_CODES,
        "min_days": MIN_SAMPLE_DAYS,
        "enough": out["unique_codes"] >= MIN_SAMPLE_CODES and dn >= MIN_SAMPLE_DAYS,
    }

    # 도구 안내대로 -7% 손절과 트레일링을 적용했을 때
    ruled = [r["ruled_pct"] for r in returns if r.get("ruled_pct") is not None]
    if ruled:
        rn = len(ruled)
        reasons = {}
        for r in returns:
            if r.get("ruled_reason"):
                reasons[r["ruled_reason"]] = reasons.get(r["ruled_reason"], 0) + 1
        held = [r["ruled_days"] for r in returns if r.get("ruled_days") is not None]
        out["rules"] = {
            "count": rn,
            "win_rate": round(sum(1 for x in ruled if x > 0) / rn * 100, 1),
            "avg_return": round(sum(ruled) / rn, 2),
            "median_return": round(sorted(ruled)[rn // 2], 2),
            "max_return": round(max(ruled), 2),
            "min_return": round(min(ruled), 2),
            # 실제로 들고 있던 날수. 대부분 손절로 일찍 빠져나오기 때문에
            # 보유 기간(days_forward)과 한참 다르다. 이 값을 빼고 위
            # avg_return을 지수와 견주면 비교가 성립하지 않는다.
            "avg_days": round(sum(held) / len(held), 1) if held else None,
            "exit_reasons": reasons,
        }
        # 들고 있던 날수만큼의 지수. 위 avg_return과 같은 조건이라
        # 이 둘의 차이만이 '종목을 잘 골랐나'에 해당한다.
        bmm = [r["bench_matched_pct"] for r in returns
               if r.get("bench_matched_pct") is not None]
        if bmm:
            bmn = len(bmm)
            out["rules"]["benchmark_matched"] = {
                "count": bmn,
                "win_rate": round(sum(1 for x in bmm if x > 0) / bmn * 100, 1),
                "avg_return": round(sum(bmm) / bmn, 2),
            }

    # 같은 날 사서 같은 기간 코스피를 들고 있었을 때
    bench = [r["bench_pct"] for r in returns if r.get("bench_pct") is not None]
    if bench:
        bn = len(bench)
        out["benchmark"] = {
            "count": bn,
            "win_rate": round(sum(1 for x in bench if x > 0) / bn * 100, 1),
            "avg_return": round(sum(bench) / bn, 2),
            "median_return": round(sorted(bench)[bn // 2], 2),
        }
    return out


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

    # 지수 히스토리. screener.py가 시장 대비 강도를 낼 때 쓰는 것과 같은
    # 출처(data/market.json)를 쓴다. 화면에 뜨는 지수와 어긋나면 안 된다.
    index_closes = {}
    try:
        mk = json.loads(Path("data/market.json").read_text(encoding="utf-8"))
        for row in (mk.get("indices", {}).get("kospi", {}).get("history") or []):
            d = str(row.get("date") or "").replace("-", "")
            if d and row.get("close"):
                index_closes[d] = row["close"]
    except Exception as e:
        print(f"  지수 히스토리를 못 읽었습니다 ({e}) — 지수 대비는 건너뜁니다")
    if index_closes:
        print(f"  지수 히스토리: {len(index_closes)}일")

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
            rets = compute_returns(picks, histories, days_forward=h,
                                   index_closes=index_closes) if picks else []
            stats[f"{h}d"] = stats_summary(rets, index_closes=index_closes,
                                           days_forward=h)
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
        # 어떤 가격으로 낸 수익률인지 파일만 봐도 알게 적어둔다. 원주가로 낸
        # 옛 파일에는 이 키가 없으므로, 숫자가 왜 달라졌는지 구분이 된다.
        "price_basis": "adjusted_close",
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
