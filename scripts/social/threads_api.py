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


def _not_found_yet(err):
    """'컨테이너가 아직 안 보인다'는 일시적 오류인지 판단한다."""
    s = str(err)
    return "4279009" in s or "cannot be found" in s


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

        self._wait_finished(creation_id)
        return self._publish(creation_id)

    def _wait_finished(self, container_id, tries=40, delay=3):
        """서버가 컨테이너를 다 처리할 때까지 기다린다.

        만든 직후에는 컨테이너가 조회조차 되지 않는 구간이 있다
        (code 24 / subcode 4279009 "Media Not Found").
        그래서 조회 실패도 '아직 준비 안 됨'으로 보고 계속 기다린다.
        """
        last = ""
        for _ in range(tries):
            try:
                info = self._get(container_id, fields="status,error_message")
            except ThreadsError as e:
                last = str(e)[:200]
                time.sleep(delay)
                continue
            status = info.get("status") or ""
            if status in ("FINISHED", "PUBLISHED"):
                return
            if status in ("ERROR", "EXPIRED"):
                raise ThreadsError(
                    f"컨테이너 처리 실패({status}): {info.get('error_message') or ''}"
                )
            last = status
            time.sleep(delay)
        raise ThreadsError(f"컨테이너 처리 시간 초과 (마지막 상태: {last})")

    def _publish(self, creation_id, tries=3, delay=10):
        """발행. 컨테이너가 아직 안 보인다는 응답이면 잠시 뒤 다시 시도한다."""
        for i in range(tries):
            try:
                published = self._post(
                    f"{self.user_id}/threads_publish", creation_id=creation_id
                )
            except ThreadsError as e:
                if i == tries - 1 or not _not_found_yet(e):
                    raise
                print(f"  쓰레드 발행 재시도 ({i + 1}/{tries - 1})")
                time.sleep(delay)
                continue
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

    def conversation(self, media_id, limit=100):
        """글 하나의 대화 전체. 답글에 달린 답글까지 평평하게 돌려준다.

        /replies가 최상위 답글만 주는 것과 달리, 여기에는 중첩된 답글도
        들어 있다. replied_to.id로 어떤 댓글에 달린 답글인지 알 수 있어
        "이미 답글이 달린 댓글"을 가려내는 데 쓴다.
        """
        fields = "id,text,username,timestamp,hide_status,replied_to,root_post,is_reply"
        return self._get(
            f"{media_id}/conversation", fields=fields, reverse="false", limit=limit
        ).get("data", [])

    # --- 검색 ---

    def keyword_search(self, keyword, limit=25, search_type="RECENT"):
        """공개 글 검색.

        주의: 앱이 threads_keyword_search 심사를 통과하기 전에는 내 글만
        돌아온다. 권한이 없다고 오류가 나는 게 아니라 조용히 범위만 좁아지므로,
        부르는 쪽에서 돌아온 작성자를 보고 판단해야 한다.

        하루 2,200건 제한이 있다. 결과가 없는 질의는 세지 않는다.
        민감어로 분류된 키워드에는 빈 배열이 돌아온다.
        """
        fields = "id,text,username,permalink,timestamp,is_reply,has_replies"
        params = {"q": keyword, "fields": fields, "limit": limit}
        if search_type:
            params["search_type"] = search_type
        return self._get("keyword_search", **params).get("data", [])

    def insights(self, media_id, metrics):
        """글 하나의 지표. 응답 원본을 그대로 돌려준다."""
        return self._get(f"{media_id}/insights", metric=",".join(metrics))

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
