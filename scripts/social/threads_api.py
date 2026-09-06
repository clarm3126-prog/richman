#!/usr/bin/env python3
"""
Threads API 클라이언트.

Meta 공식 Threads API(graph.threads.net)를 감싼다.
글쓰기는 컨테이너 생성 -> 발행 두 단계로 이뤄지고,
답글도 같은 방식에 reply_to_id만 붙인 형태다.

주의: Threads에는 DM API가 없다. 링크 전달은 공개 답글로만 가능하다.
"""
import time

import requests

from . import config

TIMEOUT = 30


class ThreadsError(RuntimeError):
    pass


class Threads:
    def __init__(self, user_id, token):
        self.user_id = user_id
        self.token = token
        self._username = None

    # --- 저수준 ---

    def _get(self, path, **params):
        params["access_token"] = self.token
        r = requests.get(f"{config.THREADS_API}/{path}", params=params, timeout=TIMEOUT)
        if r.status_code >= 300:
            raise ThreadsError(f"GET {path} -> {r.status_code} {r.text[:300]}")
        return r.json()

    def _post(self, path, **params):
        params["access_token"] = self.token
        r = requests.post(f"{config.THREADS_API}/{path}", params=params, timeout=TIMEOUT)
        if r.status_code >= 300:
            raise ThreadsError(f"POST {path} -> {r.status_code} {r.text[:300]}")
        return r.json()

    # --- 계정 ---

    def username(self):
        if self._username is None:
            me = self._get("me", fields="id,username")
            self._username = me.get("username") or ""
        return self._username

    # --- 발행 ---

    def publish_text(self, text, reply_to_id=None, image_url=None):
        """텍스트(또는 이미지) 글 발행. 발행된 미디어 ID를 돌려준다."""
        params = {"text": text}
        if image_url:
            params["media_type"] = "IMAGE"
            params["image_url"] = image_url
        else:
            params["media_type"] = "TEXT"
        if reply_to_id:
            params["reply_to_id"] = reply_to_id

        container = self._post(f"{self.user_id}/threads", **params)
        creation_id = container.get("id")
        if not creation_id:
            raise ThreadsError(f"컨테이너 생성 실패: {container}")

        # 이미지는 서버가 내려받을 시간이 필요하다
        if image_url:
            time.sleep(10)

        published = self._post(f"{self.user_id}/threads_publish", creation_id=creation_id)
        media_id = published.get("id")
        if not media_id:
            raise ThreadsError(f"발행 실패: {published}")
        return media_id

    # --- 조회 ---

    def recent_posts(self, since_ts=None, limit=25):
        """내가 올린 최근 글. since_ts는 유닉스 초."""
        params = {"fields": "id,permalink,timestamp", "limit": limit}
        if since_ts:
            params["since"] = int(since_ts)
        return self._get(f"{self.user_id}/threads", **params).get("data", [])

    def replies(self, media_id, limit=50):
        """특정 글에 달린 최상위 답글."""
        fields = "id,text,username,timestamp,hide_status,replied_to,root_post"
        return self._get(f"{media_id}/replies", fields=fields, reverse="false", limit=limit).get(
            "data", []
        )

    def permalink(self, media_id):
        try:
            return self._get(media_id, fields="permalink").get("permalink", "")
        except ThreadsError:
            return ""


def refresh_token(token):
    """장기 토큰 갱신. 최소 24시간 이상 지난 토큰만 갱신된다."""
    r = requests.get(
        f"{config.THREADS_AUTH}/refresh_access_token",
        params={"grant_type": "th_refresh_token", "access_token": token},
        timeout=TIMEOUT,
    )
    if r.status_code >= 300:
        raise ThreadsError(f"토큰 갱신 실패 {r.status_code}: {r.text[:300]}")
    return r.json()
