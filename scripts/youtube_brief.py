#!/usr/bin/env python3
"""
유튜브 대본 프롬프트에 넣을 '오늘 숫자'를 뽑는다.

docs/유튜브_대본.md의 프롬프트에는 `오늘 숫자:` 칸이 있다. 거기에 손으로
숫자를 적으면 틀리기 쉽고, 틀린 숫자가 그대로 영상에 나간다. 이 스크립트가
저장된 결과 파일에서 그대로 읽어 복붙할 수 있는 형태로 찍어준다.

**종목 이름은 일부러 출력하지 않는다.**
영상에서 종목 이름을 말하지 않는 것이 이 채널의 규칙이다. 이름을 말하면
시청자가 받아적고 창을 닫아서 사이트에 올 이유가 없어지고, 특정 종목을
지목하는 모양이 되기 때문이다. 그래서 여기서도 개수와 비율만 낸다.

사용:
  python scripts/youtube_brief.py              전체
  python scripts/youtube_brief.py --minervini  미너비니 조건별만
  python scripts/youtube_brief.py --exit       매도 시그널만
  python scripts/youtube_brief.py --backtest   백테스트 성적만
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from common import load_json  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


# 조건 이름을 영상에서 쓸 말로 바꿔둔다.
# 여기 적힌 문장을 그대로 대본에 써도 되게끔, 지표 약어를 쓰지 않는다.
TREND_LABELS = [
    ("price_above_ma50", "주가가 50일 평균선 위에 있다"),
    ("price_above_ma150", "주가가 150일 평균선 위에 있다"),
    ("price_above_ma200", "주가가 200일 평균선 위에 있다"),
    ("ma50_above_ma150", "50일 평균선이 150일 평균선 위에 있다"),
    ("ma150_above_ma200", "150일 평균선이 200일 평균선 위에 있다"),
    ("ma200_uptrend", "200일 평균선이 한 달 전보다 높다"),
    ("within_25pct_of_52w_high", "1년 최고가에서 25% 안쪽에 있다"),
    ("rs_rating_70plus", "시장보다 잘 간 정도가 상위권이다 (강도 70 이상)"),
]

SETUP_LABELS = [
    ("above_25pct_from_52w_low", "1년 최저가보다 25% 넘게 올라와 있다"),
    ("5day_tightness_10pct", "최근 5일 고가와 저가 차이가 10% 이내다"),
    ("5day_open_close_5pct", "최근 5일 시작가와 오늘 종가 차이가 5% 이내다"),
    ("trade_value_7B_1x", "최근 20일 중 하루라도 거래대금 70억을 넘겼다"),
]

# 매도 신호 이름도 영상에서 쓸 말로 바꾼다.
# exit_signals.py의 label 앞부분과 맞춰 둔다. 여기 없는 이름은 그대로 나온다.
EXIT_LABELS = {
    "MA50 거래량 동반 하락": "50일 평균선을 거래량 실려 내려왔다",
    "큰 음봉": "하루에 크게 빠지면서 거래량이 늘었다",
    "MA200 이탈": "200일 평균선 아래로 내려왔다",
    "MA21 이탈": "21일 평균선 아래로 내려왔다",
    "신고가 후 거래량 감소": "최고가를 찍은 뒤 거래량이 말랐다",
    "Failed Breakout": "올라섰다가 도로 주저앉았다",
    "MA50/150 데스크로스 임박": "50일선이 150일선을 뚫고 내려가려 한다",
    "손절선 도달": "산 값에서 7% 넘게 빠졌다",
    "수익 trail": "20% 넘게 벌었는데 21일 평균선을 깼다",
    "큰 수익 trail": "50% 넘게 벌었는데 50일 평균선을 깼다",
}


def exit_label(raw):
    """'20일 분배일 5개'처럼 숫자가 섞인 이름도 앞부분으로 맞춘다."""
    name = raw.split("(")[0].strip()
    if name in EXIT_LABELS:
        return EXIT_LABELS[name]
    if name.startswith("20일 분배일"):
        return "기관이 파는 날이 한 달에 다섯 번 넘게 나왔다"
    return name


FUND_LABELS = [
    ("eps_growth_25pct", "분기 순이익이 작년 같은 분기보다 25% 넘게 늘었다"),
    ("eps_accelerating", "이익 증가 속도가 지난 분기보다 빨라졌다"),
    ("sales_growth_15pct", "분기 매출이 작년 같은 분기보다 15% 넘게 늘었다"),
    ("op_margin_q_10pct", "이번 분기 영업이익률이 10%를 넘는다"),
    ("op_margin_annual_10pct", "작년 영업이익률이 10%를 넘는다"),
    ("op_margin_3y_avg_20pct", "3년 평균 영업이익률이 20%를 넘는다"),
]


def count_passed(results, group, key):
    """저장된 종목 중 그 조건을 통과한 개수."""
    return sum(1 for r in results if (r.get(group) or {}).get(key))


def line(label, passed, total):
    pct = (passed / total * 100) if total else 0
    return f"  {label}\n    → {passed}개 / {total}개 ({pct:.0f}%)"


def show_minervini():
    data = load_json(ROOT / "data" / "screener_results.json")
    if not data:
        print("screener_results.json 없음 — 스크리너를 먼저 돌리세요.")
        return

    results = data.get("results") or []
    saved = len(results)
    evaluated = data.get("total_evaluated", 0)
    strict = data.get("minervini_strict_count", 0)
    strong = data.get("minervini_strong_count", 0)

    print(f"[미너비니] {data.get('trading_day', '?')} 기준 · 갱신 {data.get('updated', '?')}")
    print()
    print(f"  전 종목에서 조건을 따져본 종목: {evaluated}개")
    print(f"  8개 조건을 전부 통과 (엄격): {strict}개")
    print(f"  6개 이상 + 실적 조건 2개 이상 (우량): {strong}개")
    print()

    # 결과 파일에는 점수 상위 200개만 저장된다. 통과한 종목은 무조건 들어가므로
    # 조건별 비율은 전 종목 평균보다 높게 나온다. 영상에서 "전 종목의 몇 퍼센트"로
    # 말하면 틀린 말이 되므로, 여기서 못 박아 둔다.
    print(f"  ※ 아래 조건별 숫자는 저장된 상위 {saved}개 기준입니다.")
    print(f"     전 종목 {evaluated}개 기준이 아닙니다. 영상에서 '전 종목의 몇 %'로")
    print(f"     말하면 틀립니다. '조건을 잘 통과한 {saved}개 중에서'로 말하세요.")
    print()

    print("  --- 추세 조건 8개 ---")
    for key, label in TREND_LABELS:
        print(line(label, count_passed(results, "trend_template", key), saved))
    print()
    print("  --- 살 자리 조건 4개 ---")
    for key, label in SETUP_LABELS:
        group = "trend_template" if key == "above_25pct_from_52w_low" else "setup"
        print(line(label, count_passed(results, group, key), saved))
    print()
    print("  --- 실적 조건 6개 ---")
    for key, label in FUND_LABELS:
        print(line(label, count_passed(results, "fundamentals", key), saved))
    print()

    # 몇 개 조건을 통과했는지 분포. "8개 다 통과하는 게 얼마나 드문가" 편에 쓴다.
    print("  --- 통과한 조건 개수 분포 ---")
    for n in range(8, -1, -1):
        c = sum(1 for r in results if r.get("tt_passed_count") == n)
        if c:
            print(f"    {n}개 통과: {c}개")
    print()


def show_momentum():
    data = load_json(ROOT / "data" / "momentum_results.json")
    if not data:
        return
    print(f"[모멘텀] {data.get('trading_day', '?')} 기준")
    print(f"  따져본 종목: {data.get('total_evaluated', 0)}개")
    print(f"  거래량 실린 돌파 (강세): {data.get('momentum_strong_count', 0)}개")
    print(f"  돌파 직전으로 보이는 종목: {data.get('pre_breakout_count', 0)}개")
    print(f"  오늘 오른 테마: {len(data.get('rising_themes') or [])}개")
    print(f"  시장 전체 분위기: {'좋음' if data.get('market_bullish') else '나쁨'}")
    print()


def show_exit():
    data = load_json(ROOT / "data" / "exit_signals.json")
    if not data:
        return
    results = data.get("results") or []
    print(f"[매도 시그널] {data.get('trading_day', '?')} 기준")
    print(f"  살펴본 종목: {len(results)}개")
    print(f"  당장 팔아야 하는 신호: {data.get('critical_count', 0)}개")
    print(f"  주의 신호: {data.get('warning_count', 0)}개")
    print()

    # 어떤 신호가 몇 번 떴는지. 시즌 3(파는 법) 각 편의 '오늘 숫자'로 쓴다.
    tally = {}
    for r in results:
        for sig in r.get("signals") or []:
            name = exit_label(sig.get("label") or sig.get("type") or "?")
            tally[name] = tally.get(name, 0) + 1
    if tally:
        print("  --- 오늘 뜬 신호 종류 ---")
        for name, c in sorted(tally.items(), key=lambda x: -x[1]):
            print(f"    {name}: {c}번")
        print()


def show_backtest():
    data = load_json(ROOT / "data" / "backtest_stats.json")
    if not data or data.get("status") != "ok":
        return
    print(f"[과거 자료로 확인한 성적] 갱신 {data.get('updated', '?')}")
    print("  ※ 조건에 걸린 날 사고 그냥 들고만 있었을 때입니다.")
    print("     손절선도, 따라 올리는 매도도 걸지 않은 숫자입니다.")
    print("     영상에서 이 단서를 빼면 거짓말이 됩니다.")
    print()

    names = {
        "minervini_strict": "8개 조건 전부 통과",
        "minervini_strong": "6개 이상 + 실적 조건",
        "momentum_strong": "거래량 실린 돌파",
        "pre_breakout": "돌파 직전",
    }
    # 보유 기간은 backtest.py의 HORIZONS를 따라간다. 여기서 목록을 따로 들고
    # 있으면 기간을 늘렸을 때 이쪽만 옛날 것을 보여주게 된다.
    horizons = [f"{h}d" for h in (data.get("horizons") or [30, 60])]
    ready = data.get("horizon_ready") or {}

    for key, label in names.items():
        cat = (data.get("categories") or {}).get(key) or {}
        shown = False
        for horizon in horizons:
            v = cat.get(horizon)
            if not v:
                continue
            days = horizon.replace("d", "일")
            print(f"  {label} · {days} 보유 ({v['count']}건)")
            print(f"    이긴 비율 {v['win_rate']}% · 평균 {v['avg_return']:+.1f}%"
                  f" · 가운데값 {v['median_return']:+.1f}%")
            shown = True
        if not shown:
            print(f"  {label}: 아직 데이터 없음")
    print()

    # 값이 없는 기간이 고장인지 아직 덜 모은 건지 구분해준다.
    waiting = [
        (h, info) for h, info in ready.items()
        if not info.get("ready") and info.get("first_value_on")
    ]
    if waiting:
        print("  --- 아직 기다리는 기간 ---")
        for h, info in waiting:
            days = h.replace("d", "일")
            print(f"    {days} 보유: {info['first_value_on']}부터 값이 나옵니다"
                  f" (기록을 {info['needs_snapshot_older_than_days']}일 모아야 함)")
        print("    영상에서 이 기간을 언급하려면 그때까지 기다리세요.")
        print()


def main(argv):
    only = [a for a in argv if a.startswith("--")]
    run_all = not only

    print("=" * 62)
    print(" 유튜브 대본용 오늘 숫자 — 프롬프트의 `오늘 숫자:` 칸에 붙여넣으세요")
    print(" 종목 이름은 일부러 넣지 않습니다 (영상에서 말하지 않기로 한 규칙)")
    print("=" * 62)
    print()

    if run_all or "--minervini" in only:
        show_minervini()
    if run_all or "--momentum" in only:
        show_momentum()
    if run_all or "--exit" in only:
        show_exit()
    if run_all or "--backtest" in only:
        show_backtest()


if __name__ == "__main__":
    main(sys.argv[1:])
