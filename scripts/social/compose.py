#!/usr/bin/env python3
"""
게시글 본문 작성.

우선순위: content/posts.yml의 수동 큐 -> 없으면 스크리닝 데이터로 자동 생성.
자동 생성은 screener / momentum / theme 세 종류를 날짜에 따라 돌려가며 쓴다.

쓰레드는 500자 제한, 인스타는 2200자 제한이라 같은 내용을 길이만 달리 렌더링한다.
"""
import json
from pathlib import Path

from .store import now_kst

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"

THREADS_LIMIT = 500
IG_LIMIT = 2200

DISCLAIMER = "종목 추천이 아니라 조건에 걸린 목록입니다. 판단은 각자."


def _load(name):
    p = DATA / f"{name}.json"
    if not p.exists():
        return None
    try:
        with p.open(encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _fmt_price(v):
    try:
        return f"{int(round(float(v))):,}"
    except Exception:
        return "-"


def _day_label(day):
    """20260904 -> 9월 4일"""
    d = str(day or "")
    if len(d) == 8 and d.isdigit():
        return f"{int(d[4:6])}월 {int(d[6:8])}일"
    if len(d) == 10 and d[4] == "-":
        return f"{int(d[5:7])}월 {int(d[8:10])}일"
    return d


def _stale(data, max_age_days=4):
    """trading_day가 너무 오래된 데이터로는 글을 쓰지 않는다."""
    day = (data or {}).get("trading_day")
    if not day:
        return True
    try:
        d = f"{day[:4]}-{day[4:6]}-{day[6:8]}" if len(day) == 8 else day
        from datetime import date

        y, m, dd = (int(x) for x in d.split("-"))
        return (now_kst().date() - date(y, m, dd)).days > max_age_days
    except Exception:
        return True


# --- 종류별 생성 ---


def compose_screener():
    data = _load("screener_results")
    if not data or _stale(data):
        return None
    picks = [r for r in data.get("results", []) if r.get("minervini_strong")]
    picks.sort(key=lambda r: r.get("total_score") or 0, reverse=True)
    picks = picks[:5]
    if not picks:
        return None

    items = []
    for r in picks:
        eps = (r.get("fundamentals") or {}).get("eps_growth_q_yoy")
        note = f"EPS {eps:+.0f}%" if isinstance(eps, (int, float)) else f"{r.get('market', '')}"
        items.append(
            {
                "name": r.get("name") or r.get("code"),
                "value": f"{_fmt_price(r.get('price'))}원",
                "delta": r.get("change"),
                "note": note,
                "score": r.get("total_score"),
            }
        )

    return {
        "kind": "screener",
        # badge/headline은 인스타 카드 전용이다.
        # 프로필 그리드 썸네일은 아주 작게 보이므로 클릭 전에도
        # 무슨 글인지 알 수 있게 짧고 큰 두 줄로 만든다.
        "badge": "미너비니 스크리닝",
        "headline": ["오늘 조건을", "통과한 종목"],
        "title": "오늘의 미너비니 조건 통과 종목",
        "subtitle": f"{_day_label(data.get('trading_day'))} 기준 · {len(picks)}종목",
        "items": items,
        "tail": "추세·수급·실적 조건을 전부 통과한 종목만 추렸습니다.",
    }


def compose_momentum():
    data = _load("momentum_results")
    if not data or _stale(data):
        return None
    picks = [r for r in data.get("results", []) if r.get("momentum_strong")]
    picks.sort(key=lambda r: r.get("total_score") or 0, reverse=True)
    picks = picks[:5]
    if not picks:
        return None

    items = []
    for r in picks:
        vol = r.get("vol_ratio")
        bits = []
        if r.get("pivot_breakout"):
            bits.append("피벗돌파")
        if isinstance(vol, (int, float)) and vol >= 1.5:
            bits.append(f"거래량 {vol:.1f}배")
        if r.get("ma200_cross_recent"):
            bits.append("200일선 회복")
        items.append(
            {
                "name": r.get("name") or r.get("code"),
                "value": f"{_fmt_price(r.get('price'))}원",
                "delta": r.get("change"),
                "note": " · ".join(bits[:2]) or r.get("market", ""),
                "score": r.get("total_score"),
            }
        )

    mood = "상승" if data.get("market_bullish") else "관망"
    return {
        "kind": "momentum",
        "badge": "모멘텀",
        "headline": ["거래량이 터진", "돌파 종목"],
        "title": "거래량이 터진 모멘텀 종목",
        "subtitle": f"{_day_label(data.get('trading_day'))} 기준 · 시장 {mood} 국면",
        "items": items,
        "tail": "돌파 직후 구간이라 변동성이 큽니다. 손절선을 먼저 정하세요.",
    }


def compose_theme():
    data = _load("theme_forecast")
    if not data:
        return None
    themes = data.get("top_themes") or []
    themes = sorted(themes, key=lambda t: t.get("total_score") or 0, reverse=True)[:5]
    if not themes:
        return None

    items = []
    for t in themes:
        lead = (t.get("stocks") or [{}])[0].get("name", "")
        items.append(
            {
                "name": t.get("name", ""),
                "value": f"{t.get('total_score', 0):.0f}점",
                "delta": t.get("current_change"),
                "note": f"대표주 {lead}" if lead else "",
                "score": t.get("total_score"),
            }
        )

    return {
        "kind": "theme",
        "badge": "테마 분석",
        "headline": ["이번 주", "올라오는 테마"],
        "title": "이번 주 올라오는 테마",
        "subtitle": f"{_day_label(data.get('trading_day'))} 기준 · 순위 변화와 수급으로 계산",
        "items": items,
        "tail": "테마는 빨리 식습니다. 진입보다 이탈 기준이 중요합니다.",
    }


def _news_lines(section, hours, market, header):
    """기사 제목 줄. 뉴스는 덤이라 못 가져와도 글 자체는 나가야 한다.

    news 모듈은 beautifulsoup4를 쓰는데, 발행 워크플로에 그게 빠져 있어도
    시황 글이 통째로 죽지 않도록 여기서 늦게 불러온다.
    """
    try:
        from . import news
    except Exception as e:
        print(f"  뉴스 모듈을 못 불러왔습니다 ({e}) - 기사 없이 씁니다")
        return []
    try:
        items = news.headlines(
            section, 3, within_hours=hours, boost=news.market_boost(market)
        )
    except Exception as e:
        print(f"  뉴스 수집 실패 ({e}) - 기사 없이 씁니다")
        return []
    if not items:
        print("  기준을 넘은 기사가 없어 기사 항목을 뺍니다")
        return []
    return ["", header] + news.as_lines(items)


def _fmt_idx(v):
    try:
        return f"{float(v):,.2f}"
    except Exception:
        return "-"


def _eok(amount):
    """원 단위 금액을 억으로. 순매수 금액은 억이 읽기 편하다."""
    try:
        return f"{float(amount) / 1e8:,.0f}억"
    except Exception:
        return "-"


def _top_buy(market, investor):
    """investor_top에서 코스피 순매수 1위 종목 이름과 금액."""
    row = (((market.get("investor_top") or {}).get(investor) or {}).get("KOSPI") or {})
    buys = row.get("buy") or []
    if not buys:
        return None
    top = buys[0]
    return top.get("name"), _eok(top.get("amount"))


def compose_kr_close(with_news=True):
    """오후 4시 국장 마감 요약. market.json은 15:55에 갱신된다."""
    m = _load("market")
    if not m:
        return None
    # 장이 안 열린 날에는 어제 숫자가 오늘 마감인 것처럼 나가면 안 된다.
    day = str(m.get("trading_day") or "")
    today = now_kst().strftime("%Y%m%d")
    if day != today:
        return None

    idx = m.get("indices") or {}
    kospi = idx.get("kospi") or {}
    kosdaq = idx.get("kosdaq") or {}
    if not kospi.get("value"):
        return None

    lines = [
        f"코스피 {_fmt_idx(kospi.get('value'))} ({kospi.get('change', 0):+.2f}%)",
        f"코스닥 {_fmt_idx(kosdaq.get('value'))} ({kosdaq.get('change', 0):+.2f}%)",
        "",
    ]

    flow = []
    for investor, label in (("foreign", "외국인"), ("institution", "기관")):
        top = _top_buy(m, investor)
        if top:
            flow.append(f"{label} 순매수 1위 {top[0]} {top[1]}")
    lines += flow

    counts = []
    if m.get("new_highs"):
        counts.append(f"신고가 {len(m['new_highs'])}종목")
    if m.get("volume_surges"):
        counts.append(f"거래량 급증 {len(m['volume_surges'])}종목")
    if counts:
        lines.append(" · ".join(counts))

    themes = sorted(
        (t for t in (m.get("naver_themes") or []) if isinstance(t.get("change"), (int, float))),
        key=lambda t: -t["change"],
    )
    if themes:
        lines += ["", f"가장 많이 오른 테마: {themes[0]['name']} {themes[0]['change']:+.1f}%"]

    if with_news:
        # 오늘 움직인 테마·종목 이름을 넘겨 그날 시장과 이어지는 기사를 고르게 한다.
        # 12시간으로 잡아 오늘 나온 기사만 본다.
        lines += _news_lines("market", 12, m, "오늘 나온 기사")

    lines += ["", "장 마감 기준 숫자입니다. " + DISCLAIMER]
    lines += ["", "오늘 어떤 종목 보셨어요?"]

    return {
        "kind": "kr_close",
        "title": f"{_day_label(day)} 국장 마감",
        "lines": lines,
        "no_cta": True,
    }


US_INDICES = [
    ("^GSPC", "S&P500"),
    ("^IXIC", "나스닥"),
    ("^DJI", "다우"),
    ("^VIX", "VIX"),
]


def compose_us_brief(with_news=True):
    """아침 8시 미장 브리핑. 국장 개장(9시) 전에 나간다.

    지수는 us_screener가 이미 쓰는 야후 차트 API로 가져온다.
    """
    from us_screener import fetch_ohlc  # 네트워크를 쓰므로 필요할 때만 부른다

    lines = []
    for symbol, label in US_INDICES:
        hist, cur, change = fetch_ohlc(symbol, rng="5d")
        if not hist:
            continue
        lines.append(f"{label} {_fmt_idx(cur)} ({change:+.2f}%)")
    if not lines:
        return None

    hist, cur, change = fetch_ohlc("KRW=X", rng="5d")
    if hist:
        lines.append(f"원달러 {_fmt_idx(cur)}원 ({change:+.2f}%)")

    u = _load("us_results")
    if u and not _stale(u, 4):
        mv = u.get("minervini") or {}
        total = mv.get("total_evaluated")
        strict = mv.get("minervini_strict_count")
        strong = mv.get("minervini_strong_count")
        if total and strong is not None:
            lines += [
                "",
                f"미국 {total}종목을 같은 기준으로 걸러봤습니다.",
                f"미너비니 조건 통과 {strong}개 (8개 전부 통과는 {strict}개)",
            ]

    if with_news:
        # 미국 장은 한국 새벽에 끝나므로 18시간까지 본다.
        lines += _news_lines("world", 18, _load("market"), "간밤 나온 기사")

    lines += ["", "종목 추천이 아니라 조건에 걸린 개수입니다."]
    lines += ["", "오늘 국장은 어떻게 보세요?"]

    return {
        "kind": "us_brief",
        "title": f"{_day_label(now_kst().strftime('%Y%m%d'))} 미국 시장",
        "lines": lines,
        "no_cta": True,
    }


BUILDERS = {
    "screener": compose_screener,
    "momentum": compose_momentum,
    "theme": compose_theme,
}

# 정해진 시각에만 나가는 글. 날짜 돌려쓰기(rotation)에는 넣지 않는다.
BRIEFS = {
    "kr_close": compose_kr_close,
    "us_brief": compose_us_brief,
}


def auto_compose(rotation, skip_kinds=()):
    """rotation 순서대로 돌려가며 오늘 쓸 수 있는 글을 하나 고른다."""
    if not rotation:
        rotation = ["screener", "momentum", "theme"]
    start = now_kst().timetuple().tm_yday % len(rotation)
    order = rotation[start:] + rotation[:start]
    for kind in order:
        if kind in skip_kinds:
            continue
        builder = BUILDERS.get(kind)
        if not builder:
            continue
        post = builder()
        if post:
            return post
    return None


# --- 렌더링 ---


def _delta_str(v):
    if not isinstance(v, (int, float)):
        return ""
    return f" ({v:+.1f}%)"


def render(post, platform, cfg):
    """플랫폼별 본문 문자열. 링크·유도 문구 붙이기는 _finish가 한다."""
    lines = [post["title"]]
    if post.get("subtitle"):
        lines.append(post["subtitle"])
    lines.append("")

    # 시황 글은 종목 목록이 아니라 본문을 통째로 넘긴다.
    if post.get("lines"):
        lines += post["lines"]
        return _finish(lines, post, platform, cfg)

    for i, it in enumerate(post["items"], 1):
        row = f"{i}. {it['name']}"
        if it.get("value"):
            row += f" {it['value']}"
        row += _delta_str(it.get("delta"))
        lines.append(row)
        if it.get("note"):
            lines.append(f"   {it['note']}")

    lines.append("")
    if post.get("tail"):
        lines.append(post["tail"])
    lines.append(DISCLAIMER)

    return _finish(lines, post, platform, cfg)


def _finish(lines, post, platform, cfg):
    """본문 뒤에 링크·댓글 유도·해시태그를 붙이고 길이를 맞춘다."""
    link_cfg = cfg.get("link") or {}
    post_cfg = cfg.get("post") or {}
    url = link_cfg.get("url", "")

    # 링크를 본문에 넣을지, 댓글 유도로 대신할지
    append_link = post_cfg.get("append_link", True)
    # 하루에 여러 번 나가는 시황 글까지 같은 유도 문구를 달면 반복이 심해진다
    cta = None if post.get("no_cta") else post_cfg.get("cta")

    if platform == "instagram":
        if url and append_link:
            lines.append("")
            lines.append(f"전체 목록과 차트는 프로필 링크에서 · {url}")
        if cta:
            lines.append("")
            lines.append(cta)
        tags = post_cfg.get("hashtags") or []
        if tags:
            lines.append("")
            lines.append(" ".join(tags))
        return _clip("\n".join(lines), IG_LIMIT)

    # 쓰레드
    if url and append_link:
        lines.append("")
        lines.append(url)
    if cta:
        lines.append("")
        lines.append(cta)
    return _clip("\n".join(lines), THREADS_LIMIT)


def _clip(text, limit):
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"
