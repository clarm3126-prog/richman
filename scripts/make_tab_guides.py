#!/usr/bin/env python3
"""탭별 안내 글을 데이터에서 만든다.

"이 탭은 어떻게 보나요"를 자주 받는다. 매번 손으로 쓰면 숫자가 낡는다.
오늘 몇 개가 걸렸는지, 백테스트가 어떻게 나왔는지는 매일 바뀌므로 글을
쓰는 시점의 값을 그대로 넣는다.

**지어내지 않는다.** 조건 이름은 index.html이 화면에 쓰는 그 문자열을
그대로 가져오고, 숫자는 data/*.json에서 읽는다. 설명 문장만 사람이 쓴
것이고 나머지는 전부 데이터다. 그래서 화면을 고치면 글도 같이 바뀐다.

나쁜 숫자를 숨기지 않는다. 백테스트가 지수보다 못하면 못했다고 쓴다.
조사해 본 계정 중 검증 숫자를 그대로 공개하는 곳은 없었다. 그 점이
이 계정의 차별점이므로 규칙으로 둔다.

출력: docs/탭안내_초안.md (사람이 읽고 고르는 용도)
      --queue 를 주면 content/posts.yml 대기열에 넣는다.

사용:
  python scripts/make_tab_guides.py
  python scripts/make_tab_guides.py --tab minervini
  python scripts/make_tab_guides.py --queue --after 2026-10-05
"""
import argparse
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from social import rules  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def load(name):
    try:
        return json.loads((DATA / f"{name}.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def n(v, unit=""):
    """숫자를 읽기 쉽게. 없으면 물음표 대신 빈 값을 돌려 문장을 빼게 한다."""
    if v is None:
        return None
    if isinstance(v, float):
        v = round(v, 1)
    return f"{v:,}{unit}" if isinstance(v, int) else f"{v}{unit}"


# 탭마다: 제목, 한 줄 정의, 무엇을 보는지, 데이터에서 뽑는 숫자.
#
# body()는 문단 목록을 돌려준다. 문단 안에서 줄바꿈은 rules.wrap()이
# 맡는다. 여기서는 무슨 말을 할지만 정한다.
def tab_minervini():
    sc = load("screener_results")
    bt = load("backtest_stats")
    cond = load("screener_conditions")
    tt = cond.get("tt_keys") or []
    v = (bt.get("categories", {}).get("minervini_strong", {}) or {}).get("30d") or {}
    r = v.get("rules") or {}
    idx = (v.get("period") or {}).get("index") or {}

    paras = [
        "미너비니 탭 보는 법입니다.",
        "추세가 이미 만들어진 종목만 모읍니다.",
        f"오늘 {n(sc.get('total_evaluated'))}개를 계산해서 "
        f"{n(sc.get('minervini_strong_count'))}개가 걸렸습니다.",
        f"조건은 {len(tt)}개입니다.\n초록은 통과, 회색은 못 넘은 겁니다.",
        "· 이동평균선 위에 있나\n· 순서대로 정렬됐나\n· 고점 근처인가\n· 시장보다 강한가",
        "숫자를 읽는 것보다 회색 칸을 보는 게 빠릅니다.",
    ]
    if r.get("avg_return") is not None:
        paras += [
            f"검증은 평균 {r['avg_return']}%였습니다. "
            f"같은 기간 코스피는 {idx.get('change_pct')}%였고요.",
            "좋게 나오지 않았습니다.\n그래서 손절선이 붙어 있는 겁니다.",
        ]
    return "미너비니 탭", paras


def tab_momentum():
    mo = load("momentum_results")
    bt = load("backtest_stats")
    cond = load("momentum_conditions")
    keys = cond.get("bool_keys") or []
    v = (bt.get("categories", {}).get("momentum_strong", {}) or {}).get("30d") or {}
    r = v.get("rules") or {}
    themes = mo.get("rising_themes") or []

    paras = [
        "모멘텀 탭 보는 법입니다.",
        "200일선을 막 뚫은 종목만 모읍니다.",
        f"오늘 {n(mo.get('total_evaluated'))}개 중 "
        f"{n(mo.get('momentum_strong_count'))}개가 걸렸습니다.",
    ]
    if themes:
        top = themes[0]
        paras.append(
            f"맨 위에 강세로 돌아선 테마가 깔립니다. 오늘은 {len(themes)}개고 "
            f"1위가 {top.get('delta')}계단 뛰었습니다."
        )
        paras.append("돈이 어디로 도는지 먼저 보고 종목을 봅니다.")
    paras.append(f"종목마다 신호 {len(keys)}개를 셉니다. 변동폭이 줄었는지, "
                 "거래량이 터졌는지, 저점을 높이고 있는지.")
    paras.append("미너비니가 이미 가는 종목이라면 이쪽은 막 방향을 튼 종목입니다. "
                 "그래서 둘은 잘 안 겹칩니다.")
    if r.get("avg_return") is not None:
        paras.append(f"검증은 평균 {r['avg_return']}%였습니다. 좋지 않습니다. "
                     "짧게 끊는 자리라 손절이 더 중요합니다.")
    return "모멘텀 탭", paras


def tab_stop():
    ss = load("stop_sensitivity")
    lv = {x["label"]: x for x in (ss.get("levels") or [])}
    if not lv:
        return None
    a, b = lv.get("-7%"), lv.get("안 걸면")
    paras = [
        "손절선을 몇 %로 잡느냐는 질문을 자주 받습니다.",
        f"조건 통과 {n(a['count'])}건에 손절선만 바꿔 대입해봤습니다.",
        f"-7%로 자르면 평균 {a['avg_return']}%, 승률 {a['win_rate']}%입니다.",
        f"아예 안 걸면 평균 {b['avg_return']}%, 승률 {b['win_rate']}%고요.",
        "승률은 안 거는 쪽이 높습니다. 대신 크게 깨집니다.",
        f"안 걸었을 때 제일 크게 깨진 한 건이 {b['min_return']}%였습니다. "
        f"규칙을 걸면 {a['min_return']}%까지 줄고요.",
        "자주 잘리는 대신 크게 안 깨지는 겁니다.",
        "지킬 수 있는 숫자가 더 중요합니다.",
        "여러분은 몇 %를 쓰시나요?",
    ]
    return "손절선", paras


def tab_newhighs():
    mk = load("market")
    highs = mk.get("new_highs") or []
    paras = [
        "신고가 탭 보는 법입니다.",
        f"오늘 1년 신고가를 뚫은 종목이 {n(len(highs))}개입니다.",
        "신고가는 위에 물린 사람이 없다는 뜻입니다. 파는 힘이 약합니다.",
        "다만 신고가라고 다 가는 건 아닙니다.",
        "거래량이 실렸는지를 같이 보셔야 합니다. 거래량 없이 오른 신고가는 자주 되밀립니다.",
        "이 탭은 조건을 통과했는지가 아니라 오늘 무슨 일이 있었는지를 봅니다.",
    ]
    return "신고가 탭", paras


def tab_themes():
    mk = load("market")
    mo = load("momentum_results")
    themes = mk.get("naver_themes") or []
    rising = mo.get("rising_themes") or []
    paras = [
        "테마별 탭 보는 법입니다.",
        f"테마 {n(len(themes))}개를 매일 순위로 셉니다.",
        "중요한 건 오늘 몇 % 올랐냐가 아닙니다.",
        "순위가 최근에 몇 계단 뛰었느냐입니다.",
    ]
    if rising:
        paras.append(f"오늘 강세로 돌아선 테마가 {len(rising)}개 잡혔습니다.")
    paras += [
        "이미 오른 테마를 쫓으면 늦습니다. 막 올라오기 시작한 쪽을 봅니다.",
        "테마를 먼저 보고 그 안에서 종목을 고르면 순서가 맞습니다.",
    ]
    return "테마별 탭", paras


TABS = {
    "minervini": tab_minervini,
    "momentum": tab_momentum,
    "stop": tab_stop,
    "newhighs": tab_newhighs,
    "themes": tab_themes,
}


def build(fn):
    made = fn()
    if not made:
        return None
    title, paras = made
    body = "\n\n".join(rules.wrap(p) for p in paras)
    text = f"{body}\n\n{rules.DISCLAIMER}\n\n{rules.CTA}"
    return title, text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tab", help="한 탭만")
    ap.add_argument("--queue", action="store_true", help="posts.yml 대기열에 넣는다")
    ap.add_argument("--after", default="", help="대기열에 넣을 때 발행 가능일")
    args = ap.parse_args()

    names = [args.tab] if args.tab else list(TABS)
    out, bad = [], 0
    for name in names:
        fn = TABS.get(name)
        if not fn:
            print(f"모르는 탭: {name}")
            continue
        made = build(fn)
        if not made:
            print(f"{name}: 데이터가 없어 건너뜁니다")
            continue
        title, text = made
        problems = rules.check(text)
        st = rules.stats(text)
        mark = "OK" if not problems else "손봐야 함"
        print(f"\n[{title}] {mark}  {st}")
        for p in problems:
            print(f"   - {p}")
            bad += 1
        out.append((title, text, problems))

    path = ROOT / "docs" / "탭안내_초안.md"
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("# 탭 안내 글 초안\n\n")
        f.write("`python scripts/make_tab_guides.py` 로 다시 만듭니다.\n")
        f.write("숫자는 만든 시점의 data/*.json 값입니다.\n\n")
        for title, text, problems in out:
            f.write(f"---\n\n## {title}\n\n")
            if problems:
                f.write("> 규칙 위반: " + " / ".join(problems) + "\n\n")
            f.write("```\n" + text + "\n```\n\n")
    print(f"\n저장: {path}  ({len(out)}편, 위반 {bad}건)")

    if args.queue:
        queue(out, args.after)


def queue(out, after):
    """대기열에 넣는다. 이미 같은 제목이 있으면 건너뛴다."""
    import yaml
    p = ROOT / "content" / "posts.yml"
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    have = {x.get("text", "")[:30] for x in doc.get("queue", [])}
    added = 0
    for title, text, problems in out:
        if problems:
            print(f"  건너뜀(규칙 위반): {title}")
            continue
        if text[:30] in have:
            print(f"  건너뜀(이미 있음): {title}")
            continue
        doc["queue"].append({
            "after": after or "",
            "kind": "guide",
            "platforms": ["threads"],
            "published": False,
            "text": text,
        })
        added += 1
    if added:
        p.write_text(yaml.dump(doc, allow_unicode=True, sort_keys=False, width=200),
                     encoding="utf-8")
    print(f"  대기열에 {added}편 넣었습니다")


if __name__ == "__main__":
    main()
