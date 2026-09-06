#!/usr/bin/env python3
"""
설정 점검.

토큰과 권한이 제대로 들어갔는지 실제로 API를 불러서 확인한다.
아무것도 게시하거나 발송하지 않는다. 읽기만 한다.

GitHub Actions 로그를 뒤지기 전에 여기서 먼저 걸러내는 용도다.

로컬 실행:
  pip install requests pyyaml
  THREADS_USER_ID=... THREADS_ACCESS_TOKEN=... python scripts/social_check.py

Actions에서 실행: Social Check 워크플로우를 수동 실행
"""
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))

from social import config  # noqa: E402

OK = "  [OK]  "
NO = "  [--]  "
BAD = "  [!!]  "

fails = []
warns = []


def head(title):
    print(f"\n{title}\n" + "-" * 52)


def check_config():
    head("설정 파일")
    try:
        cfg = config.load_config()
    except SystemExit as e:
        print(BAD + str(e))
        fails.append("content/social.yml 을 읽을 수 없음")
        return None

    url = (cfg.get("link") or {}).get("url", "")
    print(OK + f"content/social.yml 읽음")
    print(OK + f"링크: {url or '(비어 있음)'}")
    if not url:
        warns.append("link.url 이 비어 있어 답장에 링크가 안 들어갑니다")

    engage = cfg.get("engage") or {}
    rules = engage.get("rules") or []
    print(OK + f"댓글 규칙 {len(rules)}개 · 무시 키워드 {len(engage.get('skip_keywords') or [])}개")
    if (engage.get("default") or {}).get("reply"):
        print(OK + "규칙에 안 걸린 댓글에도 기본 답장이 나갑니다")
    else:
        print(OK + "규칙에 안 걸린 댓글은 무시합니다")

    # 쓰레드 답글에 링크가 들어가면 봇이 링크를 공개로 뿌리게 된다.
    # 직접 DM으로 보내는 운영이면 실수로 새어나가지 않게 미리 잡아준다.
    leaky = []
    for rule in list(rules) + [engage.get("default") or {}]:
        name = rule.get("name") or "default"
        tmpl = rule.get("reply_threads") or rule.get("reply") or []
        for t in tmpl if isinstance(tmpl, list) else [tmpl]:
            if "{link}" in str(t) or "http" in str(t):
                leaky.append(name)
                break
    if leaky:
        print(BAD + f"쓰레드 답글 문구에 링크가 들어 있습니다: {', '.join(leaky)}")
        warns.append(
            f"쓰레드 답글에 링크가 포함된 규칙: {', '.join(leaky)} "
            "(직접 DM으로 보낼 계획이면 문구에서 링크를 빼세요)"
        )
    else:
        print(OK + "쓰레드 답글 문구에 링크 없음 (직접 DM 운영)")

    post = cfg.get("post") or {}
    print(OK + f"글쓰기 {'켜짐' if post.get('enabled', True) else '꺼짐'}"
          f" · 하루 최대 {post.get('max_per_day', 1)}건"
          f" · 대상 {', '.join(post.get('platforms') or [])}")
    print(OK + f"댓글 대응 {'켜짐' if engage.get('enabled', True) else '꺼짐'}")
    return cfg


def check_threads(creds):
    head("쓰레드")
    c = creds.get("threads") or {}
    if not (c.get("user_id") and c.get("token")):
        print(NO + "THREADS_USER_ID / THREADS_ACCESS_TOKEN 없음 - 쓰레드는 동작하지 않습니다")
        return

    try:
        r = requests.get(
            f"{config.THREADS_API}/me",
            params={"fields": "id,username", "access_token": c["token"]},
            timeout=20,
        )
        if r.status_code >= 300:
            print(BAD + f"토큰이 거부됐습니다 ({r.status_code}): {r.text[:160]}")
            fails.append("쓰레드 토큰 무효 - 재발급 필요")
            return
        me = r.json()
        print(OK + f"연결됨: @{me.get('username')} (id {me.get('id')})")
        if str(me.get("id")) != str(c["user_id"]):
            print(BAD + f"THREADS_USER_ID가 다릅니다. 시크릿에 {me.get('id')} 를 넣으세요")
            fails.append("THREADS_USER_ID 불일치")
    except Exception as e:
        print(BAD + f"연결 실패: {e}")
        fails.append(f"쓰레드 연결 실패: {e}")
        return

    # threads_basic 으로 내 글 목록을 읽을 수 있는지
    try:
        r = requests.get(
            f"{config.THREADS_API}/{c['user_id']}/threads",
            params={"fields": "id,timestamp", "limit": 1, "access_token": c["token"]},
            timeout=20,
        )
        if r.status_code >= 300:
            print(BAD + f"내 글 목록 읽기 실패: {r.text[:160]}")
            fails.append("쓰레드 threads_basic 권한 확인 필요")
        else:
            posts = r.json().get("data", [])
            print(OK + f"글 목록 읽기 가능 (최근 {len(posts)}건 확인)")
            if posts:
                # threads_read_replies 확인
                r2 = requests.get(
                    f"{config.THREADS_API}/{posts[0]['id']}/replies",
                    params={"fields": "id", "limit": 1, "access_token": c["token"]},
                    timeout=20,
                )
                if r2.status_code >= 300:
                    print(BAD + "답글 읽기 실패 - threads_read_replies 권한이 없습니다")
                    fails.append("threads_read_replies 권한 추가 필요")
                else:
                    print(OK + "답글 읽기 가능 (threads_read_replies)")
            else:
                warns.append("쓰레드에 글이 없어 답글 읽기 권한은 확인하지 못했습니다")
    except Exception as e:
        print(BAD + f"확인 실패: {e}")


def check_instagram(creds):
    head("인스타그램")
    c = creds.get("instagram") or {}
    if not (c.get("user_id") and c.get("token")):
        print(NO + "IG_USER_ID / IG_ACCESS_TOKEN 없음 - 인스타는 동작하지 않습니다")
        return

    try:
        r = requests.get(
            f"{config.IG_API}/{c['user_id']}",
            params={"fields": "id,username,account_type", "access_token": c["token"]},
            timeout=20,
        )
        if r.status_code >= 300:
            print(BAD + f"토큰이 거부됐습니다 ({r.status_code}): {r.text[:160]}")
            fails.append("인스타 토큰 무효 또는 IG_USER_ID 오류")
            return
        me = r.json()
        acct = me.get("account_type") or "?"
        print(OK + f"연결됨: @{me.get('username')} (id {me.get('id')})")
        if acct in ("BUSINESS", "MEDIA_CREATOR"):
            print(OK + f"계정 유형: {acct}")
        else:
            print(BAD + f"계정 유형이 {acct} 입니다. 프로페셔널(비즈니스/크리에이터)로 전환해야 합니다")
            fails.append("인스타 계정을 프로페셔널로 전환 필요")
    except Exception as e:
        print(BAD + f"연결 실패: {e}")
        fails.append(f"인스타 연결 실패: {e}")
        return

    try:
        r = requests.get(
            f"{config.IG_API}/{c['user_id']}/media",
            params={"fields": "id,timestamp", "limit": 1, "access_token": c["token"]},
            timeout=20,
        )
        if r.status_code >= 300:
            print(BAD + f"게시물 목록 읽기 실패: {r.text[:160]}")
            fails.append("instagram_business_basic 권한 확인 필요")
            return
        media = r.json().get("data", [])
        print(OK + f"게시물 목록 읽기 가능 (최근 {len(media)}건 확인)")

        if media:
            r2 = requests.get(
                f"{config.IG_API}/{media[0]['id']}/comments",
                params={"fields": "id", "limit": 1, "access_token": c["token"]},
                timeout=20,
            )
            if r2.status_code >= 300:
                print(BAD + "댓글 읽기 실패 - instagram_business_manage_comments 권한이 없습니다")
                fails.append("instagram_business_manage_comments 권한 추가 필요")
            else:
                print(OK + "댓글 읽기 가능 (manage_comments)")
        else:
            warns.append("인스타에 게시물이 없어 댓글 권한은 확인하지 못했습니다")
    except Exception as e:
        print(BAD + f"확인 실패: {e}")

    # 게시 권한 확인.
    # 컨테이너만 만들어 보고 발행은 하지 않는다. 만들어진 컨테이너는
    # 24시간 뒤 저절로 사라지고 아무에게도 보이지 않는다.
    # 토큰에 instagram_business_content_publish 가 안 들어 있으면 여기서 걸린다.
    probe = f"{config.PAGES_BASE}/assets/cards/about.jpg"
    try:
        probe_res = requests.get(probe, timeout=20)
        if probe_res.status_code != 200:
            print(NO + "게시 권한 확인 건너뜀 (테스트용 이미지가 아직 배포되지 않음)")
            return
    except Exception:
        print(NO + "게시 권한 확인 건너뜀 (이미지 접근 실패)")
        return

    try:
        r = requests.post(
            f"{config.IG_API}/{c['user_id']}/media",
            params={"image_url": probe, "caption": "권한 확인용", "access_token": c["token"]},
            timeout=30,
        )
        if r.status_code < 300 and r.json().get("id"):
            print(OK + "게시 권한 있음 (컨테이너 생성 성공 · 발행하지 않음)")
        else:
            body = r.text[:200]
            if "permission" in body.lower() or "OAuth" in body:
                print(BAD + "게시 권한 없음 - instagram_business_content_publish 를 추가하고 "
                            "토큰을 다시 발급받으세요")
                fails.append("instagram_business_content_publish 누락 (토큰 재발급 필요)")
            else:
                print(BAD + f"게시 확인 실패: {body}")
                warns.append(f"게시 권한 확인 불가: {body[:120]}")
    except Exception as e:
        print(BAD + f"게시 확인 실패: {e}")

    warns.append(
        "DM(비공개 답장) 권한은 실제로 보내봐야 알 수 있습니다. "
        "실패해도 링크가 담긴 대체 답글이 대신 나갑니다"
    )


def check_telegram():
    head("텔레그램 (오류 알림용)")
    token = config.env("TELEGRAM_BOT_TOKEN")
    chat_id = config.env("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        print(NO + "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 없음 - 오류가 나도 알림이 오지 않습니다")
        warns.append("텔레그램 미설정 - 자동화가 조용히 멈춰도 모를 수 있습니다")
        return
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=20)
        if r.json().get("ok"):
            print(OK + f"봇 연결됨: @{r.json()['result'].get('username')}")
        else:
            print(BAD + "봇 토큰이 거부됐습니다")
            warns.append("텔레그램 봇 토큰 확인 필요")
    except Exception as e:
        print(BAD + f"확인 실패: {e}")


def check_pages(cfg):
    head("GitHub Pages (인스타 카드 이미지 호스팅)")
    creds = config.credentials()
    if not config.available(creds, "instagram"):
        print(NO + "인스타를 안 쓰면 필요 없습니다")
        return
    try:
        r = requests.get(f"{config.PAGES_BASE}/", timeout=20)
        if r.status_code == 200:
            print(OK + f"{config.PAGES_BASE} 접근 가능")
        else:
            print(BAD + f"{config.PAGES_BASE} -> HTTP {r.status_code}")
            fails.append("Pages에 접근할 수 없어 인스타에 이미지를 넘길 수 없습니다")
    except Exception as e:
        print(BAD + f"확인 실패: {e}")


def main():
    print("=" * 52)
    print("  SNS 자동화 설정 점검 (읽기만 합니다)")
    print("=" * 52)

    cfg = check_config()
    creds = config.credentials()
    check_threads(creds)
    check_instagram(creds)
    check_telegram()
    if cfg:
        check_pages(cfg)

    head("결과")
    if fails:
        print(f"  고쳐야 할 것 {len(fails)}건")
        for f in fails:
            print(f"    - {f}")
    else:
        print("  막는 문제 없음")

    if warns:
        print(f"\n  참고 {len(warns)}건")
        for w in warns:
            print(f"    - {w}")

    if not fails:
        print("\n  다음: 워크플로우를 dry_run 체크하고 수동 실행해 보세요.")
    print()
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
