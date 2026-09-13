#!/usr/bin/env python3
"""
댓글 달 만한 글 하루치 정리.

남의 글에 자동으로 댓글을 달지는 않는다. 그건 플랫폼이 금지하는 자동 참여이고
걸리면 계정이 정지된다. 대신 "오늘 이 글에는 보탤 말이 있다" 싶은 글만 골라
텔레그램으로 보내고, 댓글은 사람이 직접 단다.

40~70대를 보는 계정이라 이 편이 실제로 낫다. 이 연령대는 성의 없는 댓글을
금방 알아채고, 한 번 홍보 계정으로 찍히면 되돌리기 어렵다. 직접 단 댓글
다섯 개가 자동 댓글 쉰 개보다 팔로워로 이어진다.

고르는 기준은 "내가 보탤 게 있는가" 하나다.
  - 오늘 스크리너·모멘텀에 걸린 종목을 언급한 글 (줄 데이터가 있다)
  - 손절·매도·원칙·일지 이야기                   (할 말이 있다)
  - 묻는 글                                        (답할 자리가 있다)
리딩방·수익인증류는 걸러낸다. 거기 댓글을 달면 같은 부류로 읽힌다.

주의: 쓰레드 키워드 검색은 앱이 threads_keyword_search 심사를 통과하기
전에는 내 글만 돌려준다. 권한이 없어도 오류가 나지 않고 조용히 범위만
좁아지므로, 남의 글이 하나도 없으면 그 사실을 텔레그램으로 알린다.
"권한이 없다"와 "오늘 걸린 글이 없다"는 다른 상태다.

처리한 글은 data/social/digest_seen.json에 ID와 날짜만 남긴다.
저장소가 Public이라 작성자 아이디나 본문은 저장하지 않는다.

사용:
  python scripts/social_digest.py
  python scripts/social_digest.py --dry-run
"""
import argparse
import html
import json
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from social import config, store  # noqa: E402
from social.notify import tell_owner  # noqa: E402
from social.threads_api import Threads, ThreadsError  # noqa: E402

SEEN = "digest_seen"

# 텔레그램 한 통 상한은 4096자다. 여유를 두고 자른다.
TG_LIMIT = 3500

# 글 본문을 텔레그램에 실을 때 자르는 길이.
SNIPPET = 140

# 할 말이 있는 주제. 점수와 함께 "왜 골랐는지"를 그대로 쓴다.
TOPIC_HINTS = [
    (("손절", "손절가", "물타기", "존버"), 2, "손절 얘기",
     "얼마에 팔지 미리 정해두는 쪽 경험을 보태면 자연스럽다"),
    (("매도", "익절", "언제 팔", "팔까"), 2, "매도 시점 얘기",
     "오른 뒤 어디까지 내려오면 파는지, 네 기준을 말해주면 된다"),
    (("매매일지", "복기", "기록"), 2, "기록 얘기",
     "왜 샀는지 적어두면 뭐가 달라지는지가 네 얘깃거리다"),
    (("원칙", "기준", "뇌동", "충동"), 1, "원칙 얘기",
     "매일 같은 기준으로 거른다는 점을 꺼낼 자리"),
    (("신고가", "52주", "돌파", "이평선", "이동평균"), 1, "추세 얘기",
     "오늘 조건에 걸린 종목이 있으면 숫자로 보탤 수 있다"),
]

# 묻는 글은 답할 자리가 분명하다.
ASK_HINTS = ("어떻게", "어떤가요", "괜찮을까", "봐주세요", "조언", "어찌", "?")


def parse_ts(value):
    """API가 주는 시간 문자열을 UTC datetime으로."""
    if not value:
        return None
    text = value.replace("Z", "+0000")
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(text, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def age_hours(ts):
    if not ts:
        return 9999
    return (datetime.now(timezone.utc) - ts).total_seconds() / 3600


def load_tickers():
    """오늘 걸린 종목 이름 -> 코드.

    이름이 짧으면 뺀다. 두 글자 이름은 아무 문장에나 들어가서, 종목 얘기가
    아닌 글이 종목 글로 올라온다.
    """
    out = {}
    for name in ("screener_results.json", "momentum_results.json"):
        path = config.ROOT / "data" / name
        if not path.exists():
            continue
        try:
            with path.open(encoding="utf-8") as f:
                rows = json.load(f).get("results") or []
        except Exception:
            continue
        for row in rows:
            label = (row.get("name") or "").strip()
            if len(label) >= 3:
                out[label] = row.get("code") or ""
    return out


def score_post(text, tickers, cfg):
    """(점수, 고른 이유, 댓글 각도). 점수가 0이면 보낼 글이 아니다."""
    lowered = text.lower()

    for bad in cfg.get("skip_keywords") or []:
        if bad.lower() in lowered:
            return 0, [], ""

    if len(text) < int(cfg.get("min_text_length") or 40):
        return 0, [], ""

    score = 0
    reasons = []
    angles = []

    hits = [n for n in tickers if n in text]
    if hits:
        # 오늘 계산해둔 숫자가 있는 종목이다. 가장 강한 신호.
        score += 3
        shown = ", ".join(hits[:3])
        reasons.append(f"오늘 걸린 종목 언급: {shown}")
        angles.append(f"{hits[0]}이 오늘 어떤 조건에 걸렸는지 숫자로 보탤 수 있다")

    for words, weight, label, angle in TOPIC_HINTS:
        if any(w in text for w in words):
            score += weight
            reasons.append(label)
            angles.append(angle)
            break

    if any(w in text for w in ASK_HINTS):
        score += 2
        reasons.append("묻는 글")
        angles.append("답할 자리가 분명하니 먼저 답하고 근거를 짧게 붙이면 된다")

    return score, reasons, angles[0] if angles else ""


def collect(api, cfg, me, seen, tickers):
    """키워드마다 검색해서 점수 매긴 후보를 모은다."""
    max_age = float(cfg.get("max_age_hours") or 36)
    picked = {}
    others_seen = 0
    searched = 0
    failures = []

    for keyword in cfg.get("keywords") or []:
        try:
            rows = api.keyword_search(keyword)
            searched += 1
        except ThreadsError as e:
            failures.append(f"{keyword}: {str(e)[:120]}")
            continue

        for row in rows:
            author = (row.get("username") or "").lstrip("@")
            if author and author.lower() != me.lower():
                # 권한 판정용. 점수와 무관하게 '남의 글을 보긴 했는가'를 센다.
                others_seen += 1

            post_id = row.get("id")
            if not post_id or post_id in seen or post_id in picked:
                continue
            if author and author.lower() == me.lower():
                continue
            if row.get("is_reply"):
                # 답글 말고 원글에만 단다. 남의 대화에 끼어드는 모양이 된다.
                continue
            if age_hours(parse_ts(row.get("timestamp"))) > max_age:
                continue

            text = (row.get("text") or "").strip()
            score, reasons, angle = score_post(text, tickers, cfg)
            if score <= 0:
                continue

            picked[post_id] = {
                "id": post_id,
                "author": author,
                "text": text,
                "link": row.get("permalink") or "",
                "score": score,
                "reasons": reasons,
                "angle": angle,
                "keyword": keyword,
            }

    ranked = sorted(picked.values(), key=lambda x: -x["score"])
    return ranked, others_seen, searched, failures


def render(items, others_seen, searched, failures):
    """텔레그램 메시지 조각들. parse_mode가 HTML이라 본문을 반드시 이스케이프한다."""
    head = [f"<b>오늘 댓글 달 만한 글 {len(items)}건</b>"]

    if searched and not others_seen:
        head.append(
            "\n⚠️ 남의 글이 하나도 안 잡혔습니다. 쓰레드 키워드 검색은 "
            "<code>threads_keyword_search</code> 심사를 통과하기 전까지 "
            "내 글만 돌려줍니다. Meta 앱 심사를 신청해야 남의 글이 보입니다."
        )
    if failures:
        head.append("\n⚠️ 검색 실패: " + html.escape("; ".join(failures[:3])))

    blocks = ["\n".join(head)]

    for i, item in enumerate(items, 1):
        text = item["text"].replace("\n", " ").strip()
        if len(text) > SNIPPET:
            text = text[:SNIPPET] + "…"

        lines = [
            f"\n<b>{i}. @{html.escape(item['author'] or '?')}</b>",
            html.escape(text),
            f"→ {html.escape(', '.join(item['reasons']))}",
        ]
        if item["angle"]:
            lines.append(f"💬 {html.escape(item['angle'])}")
        if item["link"]:
            lines.append(html.escape(item["link"]))
        blocks.append("\n".join(lines))

    blocks.append(
        "\n댓글은 직접 달아주세요. 자동으로 달면 계정이 정지됩니다."
    )

    # 4096자 상한에 맞춰 나눈다. 한 건이 잘려서 링크만 남는 일이 없게 블록 단위로.
    chunks, cur = [], ""
    for block in blocks:
        if len(cur) + len(block) + 1 > TG_LIMIT and cur:
            chunks.append(cur)
            cur = block
        else:
            cur = f"{cur}\n{block}" if cur else block
    if cur:
        chunks.append(cur)
    return chunks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="발송 없이 화면에만 출력")
    args = ap.parse_args()

    cfg_all = config.load_config()
    cfg = (cfg_all.get("digest") or {}) if isinstance(cfg_all, dict) else {}
    if not cfg.get("enabled"):
        print("digest.enabled 가 false 입니다. 아무것도 하지 않습니다.")
        return 0

    creds = config.credentials()
    if not config.available(creds, "threads"):
        print("쓰레드 자격증명이 없습니다.")
        return 0

    api = Threads(creds["threads"]["user_id"], creds["threads"]["token"])
    try:
        me = api.username()
    except ThreadsError as e:
        print(f"내 계정 조회 실패: {e}")
        return 1

    seen = store.load(SEEN, {})
    tickers = load_tickers()
    print(f"오늘 걸린 종목 {len(tickers)}개를 기준으로 훑습니다.")

    ranked, others_seen, searched, failures = collect(api, cfg, me, seen, tickers)
    limit = int(cfg.get("max_items") or 6)
    items = ranked[:limit]

    print(f"검색 {searched}건 / 후보 {len(ranked)}건 / 보낼 글 {len(items)}건")

    if not items and not failures and others_seen:
        print("오늘은 보낼 글이 없습니다.")
        return 0

    for chunk in render(items, others_seen, searched, failures):
        if args.dry_run:
            print("-" * 60)
            print(chunk)
        else:
            tell_owner(chunk)

    if not args.dry_run:
        today = store.today_kst()
        for item in items:
            seen[item["id"]] = today
        store.save(SEEN, store.prune_handled(seen, days=30))

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
