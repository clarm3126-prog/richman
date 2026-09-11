#!/usr/bin/env python3
"""
Instagram API 클라이언트 (Instagram Login 방식).

인스타는 텍스트만으로는 글을 올릴 수 없어 항상 이미지 URL이 필요하다.
이미지는 GitHub Pages에 올린 카드 PNG를 쓴다.

DM은 '댓글에 대한 비공개 답장(private reply)'만 사용한다.
- 댓글 1건당 딱 1회
- 댓글 작성 후 7일 이내
- 팔로워에게 임의로 보내는 DM은 정책 위반이므로 구현하지 않는다
"""
import time

import requests

from . import config

TIMEOUT = 30


class InstagramError(RuntimeError):
    pass


class Instagram:
    def __init__(self, user_id, token):
        self.user_id = user_id
        self.token = token
        self._username = None

    # --- 저수준 ---

    def _get(self, path, **params):
        params["access_token"] = self.token
        r = requests.get(f"{config.IG_API}/{path}", params=params, timeout=TIMEOUT)
        if r.status_code >= 300:
            raise InstagramError(f"GET {path} -> {r.status_code} {r.text[:300]}")
        return r.json()

    def _post(self, path, **params):
        params["access_token"] = self.token
        r = requests.post(f"{config.IG_API}/{path}", params=params, timeout=TIMEOUT)
        if r.status_code >= 300:
            raise InstagramError(f"POST {path} -> {r.status_code} {r.text[:300]}")
        return r.json()

    def _post_json(self, path, payload):
        r = requests.post(
            f"{config.IG_API}/{path}",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=TIMEOUT,
        )
        if r.status_code >= 300:
            raise InstagramError(f"POST {path} -> {r.status_code} {r.text[:300]}")
        return r.json()

    # --- 계정 ---

    def username(self):
        if self._username is None:
            me = self._get(self.user_id, fields="id,username")
            self._username = me.get("username") or ""
        return self._username

    # --- 발행 ---

    def publish_image(self, image_url, caption):
        """이미지 1장 게시. 발행된 미디어 ID를 돌려준다."""
        container = self._post(
            f"{self.user_id}/media", image_url=image_url, caption=caption
        )
        creation_id = container.get("id")
        if not creation_id:
            raise InstagramError(f"컨테이너 생성 실패: {container}")
        self._wait_finished(creation_id)
        return self._publish(creation_id)

    def _wait_finished(self, container_id, tries=20, delay=3):
        """인스타가 이미지를 내려받아 처리할 때까지 기다린다."""
        for _ in range(tries):
            status = self._get(container_id, fields="status_code").get("status_code")
            if status == "FINISHED":
                return
            if status == "ERROR":
                raise InstagramError("이미지 처리 실패 (image_url 접근 불가 가능성)")
            time.sleep(delay)
        raise InstagramError("이미지 처리 시간 초과")

    def _publish(self, creation_id):
        published = self._post(f"{self.user_id}/media_publish", creation_id=creation_id)
        media_id = published.get("id")
        if not media_id:
            raise InstagramError(f"발행 실패: {published}")
        return media_id

    def publish_carousel(self, image_urls, caption):
        """여러 장을 캐러셀로 게시. 인스타는 2~10장만 허용한다.

        각 장을 is_carousel_item으로 만들고, 그 ID들을 children으로 묶어
        CAROUSEL 컨테이너를 만든 뒤 발행한다.
        """
        urls = [u for u in image_urls if u]
        if len(urls) < 2:
            raise InstagramError("캐러셀은 2장 이상이어야 합니다")
        urls = urls[:10]

        children = []
        for url in urls:
            item = self._post(
                f"{self.user_id}/media", image_url=url, is_carousel_item="true"
            )
            item_id = item.get("id")
            if not item_id:
                raise InstagramError(f"캐러셀 항목 생성 실패: {item}")
            children.append(item_id)

        for item_id in children:
            self._wait_finished(item_id)

        container = self._post(
            f"{self.user_id}/media",
            media_type="CAROUSEL",
            children=",".join(children),
            caption=caption,
        )
        creation_id = container.get("id")
        if not creation_id:
            raise InstagramError(f"캐러셀 컨테이너 생성 실패: {container}")
        self._wait_finished(creation_id)
        return self._publish(creation_id)

    # --- 조회 ---

    def recent_media(self, limit=25):
        return self._get(
            f"{self.user_id}/media", fields="id,permalink,timestamp", limit=limit
        ).get("data", [])

    def permalink(self, media_id):
        try:
            return self._get(media_id, fields="permalink").get("permalink", "")
        except InstagramError:
            return ""

    def comments(self, media_id, limit=50):
        """글에 달린 댓글. replies까지 같이 받아온다.

        replies가 있어야 "내가 이미 답글을 단 댓글"인지 알 수 있다.
        """
        fields = "id,text,username,timestamp,from,replies{id,username,from,timestamp}"
        return self._get(f"{media_id}/comments", fields=fields, limit=limit).get("data", [])

    def insights(self, media_id, metrics):
        """글 하나의 지표. 응답 원본을 그대로 돌려준다."""
        return self._get(f"{media_id}/insights", metric=",".join(metrics))

    # --- 응답 ---

    def reply_to_comment(self, comment_id, message):
        """댓글에 공개 답글을 단다."""
        return self._post(f"{comment_id}/replies", message=message).get("id")

    def private_reply(self, comment_id, text):
        """댓글 작성자에게 비공개 답장(DM)을 보낸다. 댓글당 1회, 7일 이내."""
        res = self._post_json(
            f"{self.user_id}/messages",
            {"recipient": {"comment_id": comment_id}, "message": {"text": text}},
        )
        return res.get("message_id") or res.get("recipient_id") or True


def refresh_token(token):
    """장기 토큰 갱신. 최소 24시간 이상 지난 토큰만 갱신된다."""
    r = requests.get(
        f"{config.IG_AUTH}/refresh_access_token",
        params={"grant_type": "ig_refresh_token", "access_token": token},
        timeout=TIMEOUT,
    )
    if r.status_code >= 300:
        raise InstagramError(f"토큰 갱신 실패 {r.status_code}: {r.text[:300]}")
    return r.json()
