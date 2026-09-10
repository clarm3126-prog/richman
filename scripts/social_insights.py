#!/usr/bin/env python3
"""
발행한 글의 성과 수집.

state.json에는 무엇을 언제 올렸는지만 남고 얼마나 읽혔는지는 없다.
그래서 어떤 글이 먹히는지 판단할 근거가 없었다. 쓰레드와 인스타의
인사이트 API로 조회수·좋아요·답글을 긁어와 insights.json에 쌓는다.

조회수는 시간이 지나며 늘어나므로 최근 글은 매일 다시 물어보고 덮어쓴다.

사용:
  python scripts/social_insights.py            # 수집만
  python scripts/social_insights.py --report   # 수집 + 텔레그램 요약
  python scripts/social_insights.py --dry-run  # 저장 없이 출력만
"""
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from social import config, store  # noqa: E402
from social.config import kind_label  # noqa: E402
from social.instagram_api import Instagram  # noqa: E402
from social.notify import tell_owner  # noqa: E402
from social.threads_api import Threads  # noqa: E402

STATE = "state"
INSIGHTS = "insights"

# 조회수가 더 안 움직일 만큼 지난 글은 다시 묻지 않는다
LOOKBACK_DAYS = 30

# 요약에 넣을 기간
REPORT_DAYS = 14

THREADS_METRICS = ["views", "likes", "replies", "reposts", "quotes", "shares"]
IG_METRICS = ["views", "reach", "likes", "comments", "saved", "shares", "total_interactions"]

# 플랫폼마다 이름이 달라서 비교하려면 맞춰줘야 한다
COMMON = {
    "threads": {"조회": "views", "좋아요": "likes", "댓글": "replies", "재게시": "reposts"},
    "instagram": {"조회": "views", "좋아요": "likes", "댓글": "comments"},
}


def parse_values(payload):
    """인사이트 응답에서 {지표: 값}만 뽑는다.

    보통 values[0].value에 들어 있고, 세분화를 요청하면 total_value로 온다.
    """
    out = {}
    for row in (payload or {}).get("data") or []:
        name = row.get("name")
        if not name:
            continue
        values = row.get("values") or []
        if values and isinstance(values[0], dict) and "value" in values[0]:
            out[name] = values[0]["value"]
        elif isinstance(row.get("total_value"), dict):
            out[name] = row["total_value"].get("value")
    return out


def fetch_metrics(api, media_id, metrics):
    """지표를 한 번에 요청하고, 막히면 하나씩 다시 물어본다.

    계정 상태나 미디어 종류에 따라 못 주는 지표가 있는데, 묶어서 요청하면
    그 하나 때문에 전체가 400이 난다. 그래서 실패하면 하나씩 시도해
    받을 수 있는 것만 건진다.
    """
    try:
        return parse_values(api.insights(media_id, metrics)), ""
    except Exception as e:
        first_error = str(e)

    out = {}
    for m in metrics:
        try:
            out.update(parse_values(api.insights(media_id, [m])))
        except Exception:
            continue
    return out, ("" if out else first_error[:200])


def cutoff(days):
    from datetime import timedelta

    return (store.now_kst() - timedelta(days=days)).strftime("%Y-%m-%d")


def collect(dry_run=False):
    state = store.load(STATE, {})
    posts = [p for p in state.get("posts") or [] if p.get("date", "") >= cutoff(LOOKBACK_DAYS)]
    if not posts:
        print(f"최근 {LOOKBACK_DAYS}일 발행 기록 없음")
        return []

    creds = config.credentials()
    apis = {}
    if config.available(creds, "threads"):
        apis["threads"] = Threads(creds["threads"]["user_id"], creds["threads"]["token"])
    if config.available(creds, "instagram"):
        apis["instagram"] = Instagram(
            creds["instagram"]["user_id"], creds["instagram"]["token"]
        )
    if not apis:
        print("자격증명 없음 - 건너뜀")
        return []

    rows = []
    for post in posts:
        for platform, id_key in (("threads", "threads_id"), ("instagram", "ig_id")):
            media_id = post.get(id_key)
            if not media_id or platform not in apis:
                continue
            metrics = THREADS_METRICS if platform == "threads" else IG_METRICS
            values, error = fetch_metrics(apis[platform], media_id, metrics)
            row = {
                "date": post.get("date"),
                "kind": post.get("kind") or "?",
                "platform": platform,
                "media_id": media_id,
                "metrics": values,
                "checked": store.today_kst(),
            }
            if error:
                row["error"] = error
                print(f"  ! {platform} {post.get('date')} 조회 실패: {error[:80]}")
            else:
                print(f"  {platform} {post.get('date')} {post.get('kind')}: {summarize(row)}")
            rows.append(row)

    if dry_run:
        print("\n(예행 연습이라 저장하지 않습니다)")
        return rows

    store.save(INSIGHTS, {"updated": store.today_kst(), "posts": rows})
    print(f"\n{len(rows)}건 저장")
    return rows


def summarize(row):
    """한 줄 요약. 플랫폼마다 지표 이름이 달라 공통 이름으로 바꾼다."""
    names = COMMON.get(row["platform"], {})
    parts = []
    for label, key in names.items():
        v = row["metrics"].get(key)
        if v is not None:
            parts.append(f"{label} {v}")
    return " · ".join(parts) or "지표 없음"


def by_kind(rows, platform):
    """형식별 성과. 조회수가 아니라 댓글률로 줄을 세운다.

    쓰레드는 팔로워 수가 아니라 반응으로 도달이 갈리고, 그중 댓글의
    가중치가 가장 높다. 조회수가 높아도 댓글이 안 달리는 형식은
    더 써봐야 계정이 안 큰다. 그래서 조회 대비 댓글 비율을 본다.

    조회수가 0이거나 없는 글은 비율을 낼 수 없어 뺀다.
    """
    comment_key = COMMON.get(platform, {}).get("댓글")
    buckets = {}
    for r in rows:
        if r["platform"] != platform:
            continue
        views = r["metrics"].get("views")
        comments = r["metrics"].get(comment_key)
        if not views or comments is None:
            continue
        buckets.setdefault(r["kind"], []).append((views, comments))

    out = []
    for kind, pairs in buckets.items():
        views = sum(v for v, _ in pairs)
        comments = sum(c for _, c in pairs)
        out.append(
            {
                "kind": kind,
                "n": len(pairs),
                "views": views / len(pairs),
                "comments": comments / len(pairs),
                # 글마다 조회수 차이가 커서 글별 비율을 평균 내면
                # 조회수 적은 글이 과대평가된다. 합계끼리 나눈다.
                "rate": comments / views * 100,
            }
        )
    return sorted(out, key=lambda d: -d["rate"])


def report(rows):
    recent = [r for r in rows if r.get("date", "") >= cutoff(REPORT_DAYS)]
    if not recent:
        print("요약할 글이 없습니다")
        return

    lines = [f"📊 <b>SNS 성과 (최근 {REPORT_DAYS}일)</b> · 댓글 많은 순"]

    for platform, label in (("threads", "쓰레드"), ("instagram", "인스타")):
        mine = [r for r in recent if r["platform"] == platform and r["metrics"]]
        if not mine:
            continue
        # 조회수가 아니라 댓글 순이다. 도달을 만드는 건 댓글이라
        # 조회만 높고 댓글이 없는 글을 위에 두면 판단을 그르친다.
        ckey = COMMON.get(platform, {}).get("댓글")
        mine.sort(
            key=lambda r: (
                -(r["metrics"].get(ckey) or 0),
                -(r["metrics"].get("views") or 0),
            )
        )
        lines.append(f"\n<b>{label}</b>")
        for r in mine[:5]:
            lines.append(f"{r['date'][5:]} {kind_label(r['kind'])} · {summarize(r)}")

        kinds = by_kind(recent, platform)
        if len(kinds) >= 2:
            lines.append("<i>형식별 · 댓글률 순</i>")
            for d in kinds[:5]:
                lines.append(
                    f"  {kind_label(d['kind'])} 댓글률 {d['rate']:.1f}%"
                    f" · 댓글 {d['comments']:.0f} · 조회 {d['views']:.0f} ({d['n']}건)"
                )
            best = kinds[0]
            tip = f"👉 {label}에서 댓글이 가장 잘 붙는 형식은 <b>{kind_label(best['kind'])}</b>입니다."
            if best["n"] < 3:
                tip += " (아직 {}건이라 참고만 하세요)".format(best["n"])
            lines.append(tip)

    text = "\n".join(lines)
    print("\n" + text.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", ""))
    tell_owner(text)


if __name__ == "__main__":
    args = sys.argv[1:]
    try:
        rows = collect(dry_run="--dry-run" in args)
        if "--report" in args and rows:
            report(rows)
    except Exception:
        traceback.print_exc()
        tell_owner("🛑 <b>SNS 성과 수집 오류</b>\n\n실행 로그를 확인해주세요.")
        sys.exit(1)
