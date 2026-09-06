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
        "title": "오늘의 미너비니 조건 통과 종목",
        "subtitle": f"{_day_label(data.get('trading_day'))} 기준 · {data.get('total_evaluated', 0)}종목 중 {len(picks)}개",
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
        "title": "이번 주 올라오는 테마",
        "subtitle": f"{_day_label(data.get('trading_day'))} 기준 · 순위 변화와 수급으로 계산",
        "items": items,
        "tail": "테마는 빨리 식습니다. 진입보다 이탈 기준이 중요합니다.",
    }


BUILDERS = {
    "screener": compose_screener,
    "momentum": compose_momentum,
    "theme": compose_theme,
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
    """플랫폼별 본문 문자열."""
    link_cfg = cfg.get("link") or {}
    post_cfg = cfg.get("post") or {}
    url = link_cfg.get("url", "")

    lines = [post["title"]]
    if post.get("subtitle"):
        lines.append(post["subtitle"])
    lines.append("")

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

    if platform == "instagram":
        tags = post_cfg.get("hashtags") or []
        if url:
            lines.append("")
            lines.append(f"전체 목록과 차트는 프로필 링크에서 · {url}")
        if tags:
            lines.append("")
            lines.append(" ".join(tags))
        return _clip("\n".join(lines), IG_LIMIT)

    # 쓰레드
    if url and post_cfg.get("append_link", True):
        lines.append("")
        lines.append(url)
    return _clip("\n".join(lines), THREADS_LIMIT)


def _clip(text, limit):
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"
