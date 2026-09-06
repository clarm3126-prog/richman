#!/usr/bin/env python3
"""
쓰레드 / 인스타 장기 토큰 갱신.

Meta의 장기 토큰은 60일이면 만료된다. 갱신 API를 호출하면 60일이 새로 시작된다.
(단 발급 후 최소 24시간이 지난 토큰만 갱신 가능하다.)

갱신한 토큰을 어디에 저장하느냐가 문제인데, 두 가지 방식을 지원한다.

  1. GH_PAT 시크릿이 있으면 GitHub Secrets를 API로 직접 덮어쓴다. 완전 자동.
     - PAT 권한: 이 저장소의 Secrets = Read and write
  2. GH_PAT가 없으면 새 토큰을 텔레그램으로 보내준다.
     받아서 직접 Secrets에 붙여넣으면 된다.

만료가 10일 안쪽으로 다가오면 갱신 성공 여부와 무관하게 경고를 보낸다.

사용:
  python scripts/social_refresh_token.py
"""
import base64
import os
import sys
import traceback
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))

from social import config, store  # noqa: E402
from social.instagram_api import refresh_token as ig_refresh  # noqa: E402
from social.notify import tell_owner  # noqa: E402
from social.threads_api import refresh_token as th_refresh  # noqa: E402

STATE = "tokens"
WARN_DAYS = 10

TARGETS = [
    ("threads", "THREADS_ACCESS_TOKEN", th_refresh, "쓰레드"),
    ("instagram", "IG_ACCESS_TOKEN", ig_refresh, "인스타그램"),
]


def gh_repo():
    return os.environ.get("GITHUB_REPOSITORY", "").strip()


def update_secret(name, value):
    """GitHub Actions Secret을 덮어쓴다. 성공하면 True."""
    pat = os.environ.get("GH_PAT", "").strip()
    repo = gh_repo()
    if not (pat and repo):
        return False

    try:
        from nacl import encoding, public
    except ImportError:
        print("  ! pynacl 미설치 - 시크릿 자동 갱신 불가")
        return False

    headers = {
        "Authorization": f"Bearer {pat}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    r = requests.get(
        f"https://api.github.com/repos/{repo}/actions/secrets/public-key",
        headers=headers, timeout=20,
    )
    if r.status_code >= 300:
        print(f"  ! 공개키 조회 실패 {r.status_code}: {r.text[:200]}")
        return False
    key = r.json()

    sealed = public.SealedBox(
        public.PublicKey(key["key"].encode("utf-8"), encoding.Base64Encoder())
    ).encrypt(value.encode("utf-8"))

    r = requests.put(
        f"https://api.github.com/repos/{repo}/actions/secrets/{name}",
        headers=headers,
        json={
            "encrypted_value": base64.b64encode(sealed).decode("utf-8"),
            "key_id": key["key_id"],
        },
        timeout=20,
    )
    if r.status_code >= 300:
        print(f"  ! 시크릿 저장 실패 {r.status_code}: {r.text[:200]}")
        return False
    return True


def main():
    creds = config.credentials()
    state = store.load(STATE, {})
    messages = []

    for platform, secret_name, refresher, label in TARGETS:
        token = (creds.get(platform) or {}).get("token")
        if not token:
            print(f"{label}: 토큰 없음 - 건너뜀")
            continue

        try:
            res = refresher(token)
        except Exception as e:
            print(f"{label}: 갱신 실패 - {e}")
            messages.append(f"❌ {label} 토큰 갱신 실패\n{str(e)[:200]}")
            continue

        new_token = res.get("access_token")
        expires_in = int(res.get("expires_in") or 0)
        days = expires_in // 86400
        print(f"{label}: 갱신 성공 · {days}일 남음")

        state[platform] = {"refreshed": store.today_kst(), "expires_in_days": days}

        if not new_token or new_token == token:
            continue

        if update_secret(secret_name, new_token):
            print(f"  {secret_name} 시크릿 갱신 완료")
            messages.append(f"✅ {label} 토큰 자동 갱신 ({days}일 유효)")
        else:
            messages.append(
                f"🔑 <b>{label} 새 토큰</b> ({days}일 유효)\n"
                f"GitHub Secrets의 <code>{secret_name}</code> 에 넣어주세요.\n\n"
                f"<code>{new_token}</code>"
            )

        if days <= WARN_DAYS:
            messages.append(f"⏰ {label} 토큰 만료가 {days}일 남았습니다. 확인이 필요합니다.")

    store.save(STATE, state)

    if messages:
        tell_owner("<b>SNS 토큰 점검</b>\n\n" + "\n\n".join(messages))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        tell_owner("🛑 <b>SNS 토큰 갱신 스크립트 오류</b>\n\n실행 로그를 확인해주세요.")
        sys.exit(1)
