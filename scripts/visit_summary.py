#!/usr/bin/env python3
"""
어제 방문 요약을 텔레그램으로 보낸다.

사이트가 page_hits 테이블에 방문을 한 줄씩 넣는다(index.html). 여기서는
그걸 날짜와 유입원으로 묶어 세기만 한다.

**개인을 식별하는 값은 다루지 않는다.**
page_hits에는 IP도 브라우저 정보도 없다. visitor는 브라우저가 스스로 만든
임의 번호이고, 여기서는 그 번호를 세기만 하고 밖으로 내보내지 않는다.
저장소가 Public이라 로그에도 숫자만 남긴다.

사용:
  python scripts/visit_summary.py           어제치
  python scripts/visit_summary.py --days 7  최근 7일
  python scripts/visit_summary.py --dry-run 텔레그램 발송 없이 화면에만
"""
import os
import sys
import traceback
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from common import send_message, supabase_get, supabase_headers  # noqa: E402

KST = timezone(timedelta(hours=9))

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

# 유입원 이름을 읽기 좋게. 여기 없는 값은 그대로 보여준다.
SOURCE_LABELS = {
    "threads": "쓰레드",
    "instagram": "인스타",
    "youtube": "유튜브",
    "blog": "블로그",
    "telegram": "텔레그램",
    "bio": "프로필 링크",
    "": "직접 방문",
}


def normalize_src(src):
    """유입원을 하나로 묶는다.

    꼬리표로 들어온 'youtube'와 referrer로 잡힌 'm.youtube.com'이 따로
    세어지면 숫자가 쪼개져 쓸모가 없다. 같은 곳이면 같은 이름으로 묶는다.
    """
    s = (src or "").strip().lower()
    if s in SOURCE_LABELS:
        return s
    for key in SOURCE_LABELS:
        if key and key in s:
            return key
    return s


def fetch_hits(since_day):
    """since_day 이후의 방문 기록. 한 번에 최대 10000줄."""
    headers = supabase_headers(SERVICE_KEY)
    return supabase_get(
        SUPABASE_URL, headers, "page_hits",
        {
            "select": "day,src,visitor",
            "day": f"gte.{since_day}",
            "limit": "10000",
        },
    )


def connected_users():
    """텔레그램을 연결한 사용자 수. 못 읽으면 None.

    방문 수보다 이쪽이 실제 지표다. 방문은 스쳐 지나가지만 연결은 남는다.
    개수만 세고 chat_id는 가져오지 않는다.
    """
    try:
        headers = {**supabase_headers(SERVICE_KEY), "Prefer": "count=exact"}
        import requests

        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/profiles",
            headers={**headers, "Range": "0-0"},
            params={"select": "id", "telegram_chat_id": "not.is.null"},
            timeout=20,
        )
        if r.status_code >= 300:
            return None
        # Content-Range: 0-0/42 형식에서 전체 개수만 뽑는다
        rng = r.headers.get("Content-Range", "")
        if "/" in rng:
            total = rng.split("/")[-1]
            return int(total) if total.isdigit() else None
    except Exception:
        pass
    return None


def summarize(hits, day):
    """하루치 방문 집계. (방문자 수, 조회 수, 유입원별 방문자 수)."""
    rows = [h for h in hits if h.get("day") == day]
    visitors = {h.get("visitor") for h in rows if h.get("visitor")}
    by_source = Counter()
    seen = set()
    for h in rows:
        v = h.get("visitor")
        src = normalize_src(h.get("src"))
        # 같은 사람이 여러 번 들어와도 유입원별로는 한 번만 센다
        key = (v, src)
        if key in seen:
            continue
        seen.add(key)
        by_source[src] += 1
    return len(visitors), len(rows), by_source


def label(src):
    return SOURCE_LABELS.get(src) or src or "직접 방문"


def main(days=1, dry_run=False):
    if not (SUPABASE_URL and SERVICE_KEY):
        print("SUPABASE_URL / SUPABASE_SERVICE_KEY 없음 — 건너뜀")
        return

    today = datetime.now(KST).date()
    since = today - timedelta(days=days + 1)

    try:
        hits = fetch_hits(since.strftime("%Y-%m-%d"))
    except Exception as e:
        print(f"방문 기록 조회 실패: {e}")
        return

    print(f"기록 {len(hits)}줄 ({since} 이후)")

    lines = ["📈 <b>종목노트 방문</b>\n"]
    any_visit = False

    for back in range(1, days + 1):
        day = (today - timedelta(days=back)).strftime("%Y-%m-%d")
        people, views, by_source = summarize(hits, day)
        if people:
            any_visit = True
        head = f"<b>{day}</b> — {people}명 · {views}회"
        print(f"  {head.replace('<b>', '').replace('</b>', '')}")
        lines.append(head)
        if by_source:
            parts = [
                f"{label(src)} {n}"
                for src, n in by_source.most_common()
            ]
            lines.append("  " + " · ".join(parts))
            print("    " + " · ".join(parts))
        lines.append("")

    if not any_visit:
        lines.append("아직 방문 기록이 없습니다.")
        lines.append("사이트에 집계 코드가 올라갔는지 확인해 주세요.")

    n = connected_users()
    if n is not None:
        lines.append(f"🔗 텔레그램 연결: <b>{n}명</b>")
        print(f"  텔레그램 연결: {n}명")

    text = "\n".join(lines).strip()

    if dry_run:
        print("\n[예행] 보낼 내용:\n")
        print(text)
        return
    if not (BOT_TOKEN and CHAT_ID):
        print("텔레그램 설정 없음 — 발송 건너뜀")
        return
    ok, why = send_message(BOT_TOKEN, CHAT_ID, text)
    print(f"텔레그램 발송: {'성공' if ok else why}")


if __name__ == "__main__":
    argv = sys.argv[1:]
    n_days = 1
    if "--days" in argv:
        try:
            n_days = max(1, int(argv[argv.index("--days") + 1]))
        except (IndexError, ValueError):
            n_days = 1
    try:
        main(days=n_days, dry_run="--dry-run" in argv)
    except Exception:
        traceback.print_exc()
        sys.exit(1)
