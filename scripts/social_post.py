#!/usr/bin/env python3
"""
쓰레드 / 인스타그램 자동 글쓰기.

두 단계로 나뉜다.
  prepare : 무엇을 올릴지 정하고, 인스타용 카드 PNG를 만든다.
  publish : GitHub Pages에 카드가 올라간 걸 확인한 뒤 실제로 발행한다.

인스타는 텍스트만으로 글을 올릴 수 없어서 이미지가 반드시 필요하고,
그 이미지는 외부에서 접근 가능한 URL이어야 한다. 그래서 카드를 먼저
저장소에 커밋해 Pages로 서빙한 다음 그 URL을 인스타에 넘긴다.

사용:
  python scripts/social_post.py prepare
  python scripts/social_post.py publish
  python scripts/social_post.py prepare --dry-run
"""
import hashlib
import sys
import time
import traceback
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))

from social import card, compose, config, store  # noqa: E402
from social.instagram_api import Instagram, InstagramError  # noqa: E402
from social.notify import tell_owner  # noqa: E402
from social.threads_api import Threads  # noqa: E402

PENDING = "pending"
STATE = "state"


def text_key(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _as_list(value):
    """수동 큐의 image는 문자열 하나여도 되고 여러 장 목록이어도 된다."""
    if not value:
        return []
    return list(value) if isinstance(value, list) else [value]


def pick_from_queue(state):
    """수동 큐에서 아직 안 올린 글 하나."""
    done = set(state.get("queue_done") or [])
    today = store.today_kst()
    for item in config.load_post_queue():
        if not isinstance(item, dict) or item.get("published"):
            continue
        text = (item.get("text") or "").strip()
        if not text or text_key(text) in done:
            continue
        if item.get("after") and str(item["after"]) > today:
            continue
        return item
    return None


def prepare(dry_run=False):
    cfg = config.load_config()
    post_cfg = cfg.get("post") or {}
    if not post_cfg.get("enabled", True):
        print("자동 글쓰기 꺼짐 (post.enabled=false)")
        return

    state = store.load(STATE, {"posts": [], "queue_done": []})
    today = store.today_kst()
    today_count = sum(1 for p in state.get("posts", []) if p.get("date") == today)
    cap = int(post_cfg.get("max_per_day") or 1)
    if today_count >= cap and not dry_run:
        print(f"오늘 이미 {today_count}건 발행 (상한 {cap}) - 건너뜀")
        return

    platforms = list(post_cfg.get("platforms") or ["threads"])
    link_cfg = cfg.get("link") or {}

    # 자격증명이 없는 플랫폼은 애초에 대상에서 뺀다.
    # 특히 인스타는 카드 PNG를 만들어 저장소에 커밋하므로,
    # 연결도 안 된 상태에서 쓸모없는 이미지가 쌓이는 걸 막는다.
    creds = config.credentials()
    ready = [p for p in platforms if config.available(creds, p)]
    if ready != platforms:
        skipped = [p for p in platforms if p not in ready]
        print(f"자격증명 없어 제외: {', '.join(skipped)}")
    if not ready and not dry_run:
        print("발행 가능한 플랫폼이 없습니다 (시크릿을 확인하세요)")
        return
    # 예행 연습은 자격증명이 없어도 본문을 보여준다
    platforms = ready or platforms

    manual = pick_from_queue(state)
    if manual:
        text = manual["text"].strip()
        targets = [p for p in (manual.get("platforms") or platforms) if p in platforms]
        plan = {
            "source": "manual",
            "kind": "manual",
            "key": text_key(text),
            "platforms": targets,
            "texts": {p: text for p in targets},
            "image_rels": _as_list(manual.get("image")),
        }
        print(f"수동 큐 사용: {text[:40]}...")
    else:
        recent_kinds = [p.get("kind") for p in state.get("posts", [])[-2:]]
        post = compose.auto_compose(post_cfg.get("rotation"), skip_kinds=recent_kinds)
        if not post:
            post = compose.auto_compose(post_cfg.get("rotation"))
        if not post:
            print("올릴 만한 데이터가 없습니다 (스크리닝 결과가 비었거나 오래됨)")
            return

        plan = {
            "source": "auto",
            "kind": post["kind"],
            "platforms": platforms,
            "texts": {p: compose.render(post, p, cfg) for p in platforms},
            "image_rels": [],
        }

        if "instagram" in platforms:
            brand = link_cfg.get("label", "종목노트")
            url = link_cfg.get("url", "")

            # 1번째 장: 그날의 종목 카드
            rel = "assets/cards/{}-{}.png".format(today, post["kind"])
            card.render_card(post, config.ROOT / rel, brand=brand, url=url)
            plan["image_rels"].append(rel)
            print(f"카드 생성: {rel}")

            # 2번째 장: 고정 소개 카드.
            # 내용이 고정이라 같은 PNG가 나오고, 바뀌지 않으면 git이 커밋하지 않는다.
            about = post_cfg.get("about_card")
            if about:
                about_rel = "assets/cards/about.png"
                card.render_about_card(
                    about, config.ROOT / about_rel, brand=brand, url=url
                )
                plan["image_rels"].append(about_rel)
                print(f"소개 카드 생성: {about_rel}")

            gone = card.prune_cards()
            if gone:
                print(f"오래된 카드 {gone}장 정리")

    plan["image_urls"] = [f"{config.PAGES_BASE}/{r}" for r in plan.get("image_rels") or []]

    if dry_run:
        for platform, text in plan["texts"].items():
            print(f"\n===== {platform} ({len(text)}자) =====\n{text}")
        for i, u in enumerate(plan.get("image_urls") or [], 1):
            print(f"\n인스타 {i}번째 장: {u}")
        return

    store.save(PENDING, plan)
    print("발행 예정 저장 완료 ({})".format(", ".join(plan["platforms"])))


def wait_for_image(url, tries=20, delay=15):
    """GitHub Pages 배포가 끝나 이미지가 실제로 열릴 때까지 기다린다."""
    for i in range(tries):
        try:
            r = requests.get(url, timeout=20)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
                print(f"  이미지 확인됨 ({i * delay}초 대기)")
                return True
        except Exception:
            pass
        time.sleep(delay)
    return False


def publish():
    plan = store.load(PENDING, {})
    if not plan or not plan.get("texts"):
        print("발행할 내용 없음")
        return

    creds = config.credentials()
    state = store.load(STATE, {"posts": [], "queue_done": []})
    record = {"date": store.today_kst(), "kind": plan.get("kind") or plan.get("source")}
    if plan.get("image_rels"):
        record["images"] = plan["image_rels"]

    image_urls = plan.get("image_urls") or []
    images_ready = None
    problems = []

    for platform in plan["platforms"]:
        text = plan["texts"].get(platform)
        if not text:
            continue
        if not config.available(creds, platform):
            print(f"{platform}: 자격증명 없음 - 건너뜀")
            continue

        try:
            if platform == "threads":
                api = Threads(creds["threads"]["user_id"], creds["threads"]["token"])
                media_id = api.publish_text(text)
                record["threads_id"] = media_id
                print(f"쓰레드 발행 완료: {media_id}")

            elif platform == "instagram":
                if not image_urls:
                    print("인스타: 이미지가 없어 건너뜀 (인스타는 이미지 필수)")
                    continue
                # Pages 배포가 끝나야 인스타가 이미지를 내려받을 수 있다
                if images_ready is None:
                    images_ready = all(wait_for_image(u) for u in image_urls)
                if not images_ready:
                    raise InstagramError(f"이미지 URL이 아직 열리지 않음: {image_urls}")

                api = Instagram(creds["instagram"]["user_id"], creds["instagram"]["token"])
                if len(image_urls) >= 2:
                    media_id = api.publish_carousel(image_urls, text)
                    print(f"인스타 캐러셀 발행 완료 ({len(image_urls)}장): {media_id}")
                else:
                    media_id = api.publish_image(image_urls[0], text)
                    print(f"인스타 발행 완료: {media_id}")
                record["ig_id"] = media_id

        except Exception as e:
            msg = f"{platform} 발행 실패: {e}"
            print(f"  ! {msg}")
            problems.append(msg)

    if record.get("threads_id") or record.get("ig_id"):
        state.setdefault("posts", []).append(record)
        state["posts"] = state["posts"][-60:]
        if plan.get("source") == "manual" and plan.get("key"):
            state.setdefault("queue_done", []).append(plan["key"])
            state["queue_done"] = state["queue_done"][-200:]
        store.save(STATE, state)

    store.save(PENDING, {})

    if problems:
        tell_owner("⚠️ <b>SNS 자동 발행 문제</b>\n\n" + "\n".join(problems[:5]))


if __name__ == "__main__":
    args = sys.argv[1:]
    stage = args[0] if args and not args[0].startswith("-") else "prepare"
    try:
        if stage == "publish":
            publish()
        else:
            prepare(dry_run="--dry-run" in args)
    except Exception:
        traceback.print_exc()
        tell_owner("🛑 <b>SNS 자동 발행 스크립트 오류</b>\n\n실행 로그를 확인해주세요.")
        sys.exit(1)
