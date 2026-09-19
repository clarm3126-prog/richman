#!/usr/bin/env python3
"""
쓰레드 / 인스타그램 자동 글쓰기.

두 단계로 나뉜다.
  prepare : 무엇을 올릴지 정하고, 인스타용 카드 이미지를 만든다.
  publish : GitHub Pages에 카드가 올라간 걸 확인한 뒤 실제로 발행한다.

인스타는 텍스트만으로 글을 올릴 수 없어서 이미지가 반드시 필요하고,
그 이미지는 외부에서 접근 가능한 URL이어야 한다. 그래서 카드를 먼저
저장소에 커밋해 Pages로 서빙한 다음 그 URL을 인스타에 넘긴다.

사용:
  python scripts/social_post.py prepare
  python scripts/social_post.py publish
  python scripts/social_post.py prepare --dry-run
  python scripts/social_post.py prepare --only=instagram   # 한 플랫폼만
  python scripts/social_post.py prepare --force            # 하루 상한 무시
  python scripts/social_post.py prepare --auto             # 큐 무시, 자동 글만
  python scripts/social_post.py prepare --queue-only       # 큐에 글 있을 때만
"""
import hashlib
import sys
import time
import traceback
from datetime import timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))

from social import card, compose, config, figures, rules, store  # noqa: E402
from social.instagram_api import Instagram, InstagramError  # noqa: E402
from social.notify import tell_owner  # noqa: E402
from social.threads_api import Threads  # noqa: E402

PENDING = "pending"
STATE = "state"

# 인스타가 게시를 막았을 때 쉬는 기간
PAUSE_DAYS = 3

# "We restrict certain activity to protect our community" 계열 응답.
# 단순 횟수 초과가 아니라 스팸 의심 차단이라 바로 재시도하면 더 나빠진다.
BLOCK_MARKERS = ("2207051", "action is blocked", "restrict certain activity")


def is_blocked(err):
    text = str(err).lower()
    return any(m.lower() in text for m in BLOCK_MARKERS)


def latest_media_id(api):
    """계정의 가장 최근 게시물 ID. 조회에 실패하면 빈 문자열."""
    try:
        media = api.recent_media(limit=1)
        return media[0]["id"] if media else ""
    except Exception:
        return ""


def text_key(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _as_list(value):
    """수동 큐의 image는 문자열 하나여도 되고 여러 장 목록이어도 된다."""
    if not value:
        return []
    return list(value) if isinstance(value, list) else [value]


def in_window(spec, now=None):
    """지금이 발행해도 되는 시각인가. spec 은 "HH:MM-HH:MM" (KST).

    GitHub 예약은 최대 일곱 시간까지 밀린다. 19:40 에 걸어 둔 글이 새벽
    두 시에 나간 날이 있었다. 새벽에 올라간 글은 조용히 묻힌다 — 쓰레드는
    올린 뒤 15~30분 안에 붙는 반응으로 더 퍼뜨릴지를 정하기 때문에,
    아무도 안 깨어 있는 시각에 나가면 조회 수만 적은 게 아니라 알고리즘이
    아예 안 집어 간다.

    그래서 늦게 뜬 실행은 올리지 않고 넘긴다. 대신 창 안에 크론을 여러 개
    걸어 두어, 그중 제때 뜬 실행이 올리게 한다.

    자정을 넘는 창(22:00-02:00)도 받는다.
    """
    if not spec:
        return True, ''
    try:
        a, b = spec.split('-')
        ah, am = (int(x) for x in a.split(':'))
        bh, bm = (int(x) for x in b.split(':'))
    except ValueError:
        raise SystemExit('시간 창 형식이 이상합니다: %s (예: 17:00-22:30)' % spec)
    now = now or store.now_kst()
    cur = now.hour * 60 + now.minute
    lo, hi = ah * 60 + am, bh * 60 + bm
    ok = (lo <= cur <= hi) if lo <= hi else (cur >= lo or cur <= hi)
    return ok, '%02d:%02d' % (now.hour, now.minute)


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


def prepare(dry_run=False, only=None, force=False, auto=False, queue_only=False,
            brief=None, window=None):
    """무엇을 올릴지 정한다.

    수동 큐는 자동 생성보다 항상 먼저 쓰이므로, 큐에 글이 쌓여 있으면
    그동안 스크리너 글이 한 건도 안 나간다. 둘 다 매일 올리려고 하루에
    두 번 실행하는데, 어느 쪽을 뽑을지는 이 두 스위치로 가른다.

      auto=True        큐를 무시하고 자동 생성 글만 만든다
      queue_only=True  큐에 글이 있을 때만 만든다 (없으면 아무것도 안 함)
      brief="kr_close" 정해진 시각의 시황 글만 만든다 (큐도 자동 생성도 안 봄)

    큐가 비면 queue_only 실행이 그냥 넘어가므로 하루 1건으로 돌아간다.
    """
    cfg = config.load_config()
    post_cfg = cfg.get("post") or {}
    if not post_cfg.get("enabled", True):
        print("자동 글쓰기 꺼짐 (post.enabled=false)")
        return

    # 밀려서 늦게 뜬 실행은 여기서 돌려보낸다. 손으로 누른 실행(--force)과
    # 예행 연습(--dry-run)은 그대로 통과시킨다.
    ok, hhmm = in_window(window)
    if not ok and not dry_run and not force:
        print("지금 %s 는 발행 시간대(%s)가 아닙니다 - 건너뜀" % (hhmm, window))
        print("  예약이 밀려 늦게 뜬 실행입니다. 창 안의 다음 실행이 올립니다.")
        return

    state = store.load(STATE, {"posts": [], "queue_done": []})
    today = store.today_kst()

    # **하루 몇 건이라는 상한은 두지 않는다.**
    #
    # 예전에는 시황 글을 뺀 하루 발행 수를 세어 상한(2건)과 견줬다. 그런데
    # 사람이 손으로 올린 글도 같이 세어져서, 그날 자동 글이 통째로 막혔다.
    # 2026-09-15 에는 밀린 예약이 새벽에 몰려 터지면서 그날 몫을 새벽에 다
    # 써버렸고, 정작 저녁에 새 데이터로 나가야 할 글이 조용히 건너뛰어졌다.
    # 수를 세는 방식으로는 "사람이 올린 것"과 "자동이 올린 것"을 가를 수 없다.
    #
    # 대신 같은 종류 글이 하루에 두 번 나가는 것만 막는다. 이게 원래 막고
    # 싶었던 일이다. 워크플로가 늦게 두 번 떠도 같은 글이 겹쳐 올라가지
    # 않고, 사람이 다른 글을 올린 것과는 무관해진다.
    kind_today = {
        p.get("kind") for p in state.get("posts", [])
        if p.get("date") == today
    }
    if brief:
        if brief in kind_today and not dry_run and not force:
            print(f"오늘 {brief} 글은 이미 나갔습니다 - 건너뜀")
            return

    platforms = list(post_cfg.get("platforms") or ["threads"])
    # 수동 실행에서 한 플랫폼만 테스트하고 싶을 때 (--only=instagram)
    if only:
        platforms = [p for p in platforms if p == only]
        if not platforms:
            print(f"'{only}' 는 설정의 platforms 에 없습니다")
            return
        print(f"{only} 에만 발행합니다")
    link_cfg = cfg.get("link") or {}

    # 자격증명이 없는 플랫폼은 애초에 대상에서 뺀다.
    # 특히 인스타는 카드 이미지를 만들어 저장소에 커밋하므로,
    # 연결도 안 된 상태에서 쓸모없는 이미지가 쌓이는 걸 막는다.
    creds = config.credentials()
    ready = [p for p in platforms if config.available(creds, p)]
    if ready != platforms:
        skipped = [p for p in platforms if p not in ready]
        print(f"자격증명 없어 제외: {', '.join(skipped)}")
    if not ready and not dry_run:
        print("발행 가능한 플랫폼이 없습니다 (시크릿을 확인하세요)")
        return
    # 인스타가 게시를 차단한 직후에는 재시도하지 않는다.
    # 매일 컨테이너를 만들어대면 차단 신호만 더 쌓인다.
    paused = state.get("instagram_paused_until")
    if paused and paused >= today and "instagram" in ready:
        print(f"인스타 일시 중지 중 ({paused}까지) - 제외")
        ready = [p for p in ready if p != "instagram"]

    # 예행 연습은 자격증명이 없어도 본문을 보여준다
    platforms = ready or platforms

    manual = None if (auto or brief) else pick_from_queue(state)
    if queue_only and not manual:
        print("큐에 올릴 글이 없습니다 - 건너뜀 (--queue-only)")
        return

    if brief:
        builder = compose.BRIEFS.get(brief)
        if not builder:
            print(f"모르는 시황 글: {brief} (쓸 수 있는 것: {', '.join(compose.BRIEFS)})")
            return
        post = builder()
        if not post:
            print(f"{brief} 글에 쓸 데이터가 없습니다 - 건너뜀")
            return
        # 카드를 만들지 않으므로 쓰레드에만 올린다. 인스타는 이미지가 있어야
        # 하고, 하루 네 번 올리면 계정이 시끄러워진다.
        targets = [pl for pl in platforms if pl == "threads"]
        if not targets:
            print("쓰레드를 쓸 수 없어 시황 글을 건너뜁니다")
            return
        plan = {
            "source": "brief",
            "kind": post["kind"],
            "platforms": targets,
            "texts": {pl: compose.render(post, pl, cfg) for pl in targets},
            "image_rels": [],
        }
        print(f"시황 글: {post['title']}")
    elif manual:
        # 발행 직전에 숫자를 현재 값과 맞춘다. 백테스트가 매일 다시 돌아
        # 대기열에서 2~3주 기다리는 사이에 숫자가 낡는다. 작은 차이는
        # 넘어가도 되지만, 지수 수익률처럼 움직이면 글의 주장이 바뀐다.
        fig_notes = []
        # 자동 글에는 있던 하루 한 번 잠금이 대기열 글에는 없었다. 창 안에
        # 크론을 여러 개 걸면 그중 둘이 제때 떠서 서로 다른 글 두 편이
        # 같은 날 나간다. kind 로는 못 막는다 — 대기열 글은 kind 가 제각각
        # (lesson·promo·trade_log)이라 서로 다른 값으로 잡히기 때문이다.
        if any(p.get("source") == "manual" for p in state.get("posts", [])
               if p.get("date") == today) and not dry_run and not force:
            print("오늘 대기열 글은 이미 나갔습니다 - 건너뜀")
            return
        raw_text = manual["text"].strip()
        text, fig_notes = figures.refresh(raw_text, manual.get("figures"))
        for note in fig_notes:
            print(f"  [숫자] {note}")
        targets = [p for p in (manual.get("platforms") or platforms) if p in platforms]
        # 인스타는 해시태그로 도달이 갈리므로 자동 글과 같은 태그를 붙인다.
        # key는 태그를 뺀 본문으로 잡아야 설정에서 태그를 바꿔도 이미 올린
        # 글이 다시 올라가지 않는다.
        tags = " ".join(post_cfg.get("hashtags") or [])
        plan = {
            "source": "manual",
            # 큐 항목이 kind를 적어두면 그대로 쓴다. 안 적으면 manual이다.
            # 이게 없으면 무료배포글·매매기록·질문글이 전부 manual 하나로
            # 뭉쳐서, 성과 요약에서 어떤 형식이 먹혔는지 구분할 수 없다.
            "kind": (manual.get("kind") or "manual").strip(),
            # 숫자를 고쳐도 같은 글이다. 키는 YAML 원문으로 잡아야
            # 다음 실행 때 안 나간 글로 되살아나지 않는다.
            "key": text_key(raw_text),
            # 쓰레드는 기본이 글만이다. 사진을 붙이면 첫 화면에서 글이 접혀
            # 읽히는 양이 줄기 때문에, 사진이 본론인 글에만 켠다.
            # 켜면 image의 첫 장이 쓰레드에도 올라간다.
            "threads_image": bool(manual.get("threads_image")),
            "platforms": targets,
            "texts": {
                p: (f"{text}\n\n{tags}" if p == "instagram" and tags else text)
                for p in targets
            },
            "image_rels": _as_list(manual.get("image")),
            "figure_notes": fig_notes,
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
        # 같은 종류가 오늘 이미 나갔으면 건너뛴다. 크론이 늦게 두 번 떠도
        # 같은 모양의 글이 겹쳐 올라가지 않는다. 사람이 손으로 올린 다른
        # 종류 글은 여기 안 걸린다.
        if post["kind"] in kind_today and not dry_run and not force:
            print(f"오늘 {post['kind']} 글은 이미 나갔습니다 - 건너뜀")
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

            # 카드에는 주소를 찍지 않는다 (url="").
            # 링크는 댓글에 반응해서 보내는 구조인데, 카드에 주소가 보이면
            # 댓글을 달 이유가 없어진다. 인스타는 캡션 링크가 클릭되지도
            # 않아서 주소를 노출해도 유입은 안 생기고 댓글만 줄어든다.

            # 1번째 장: 그날의 종목 카드
            rel = "assets/cards/{}-{}.jpg".format(today, post["kind"])
            card.render_card(post, config.ROOT / rel, brand=brand, url="")
            plan["image_rels"].append(rel)
            print(f"카드 생성: {rel}")

            # 2번째 장: 고정 소개 카드.
            # 내용이 고정이라 같은 이미지가 나오고, 바뀌지 않으면 git이 커밋하지 않는다.
            about = post_cfg.get("about_card")
            if about:
                about_rel = "assets/cards/about.jpg"
                card.render_about_card(
                    about, config.ROOT / about_rel, brand=brand, url=""
                )
                plan["image_rels"].append(about_rel)
                print(f"소개 카드 생성: {about_rel}")

            gone = card.prune_cards()
            if gone:
                print(f"오래된 카드 {gone}장 정리")

    plan["image_urls"] = [f"{config.PAGES_BASE}/{r}" for r in plan.get("image_rels") or []]

    # 글쓰기 규칙 검사. 발행을 막지는 않는다 - 하루 한 건이 조용히 사라지는
    # 것보다, 어긋난 채로 나가고 알림이 오는 편이 낫다.
    #
    # 수동 큐 글은 사람이 쓴 산문이라 줄 모양까지 본다. 자동 글은 종목 줄
    # 길이를 사람이 줄일 수 없으므로 글자 수와 쓰면 안 되는 말만 본다.
    threads_text = plan["texts"].get("threads")
    if threads_text:
        plan["warnings"] = rules.check(threads_text, prose=plan["source"] == "manual")
        # 숫자를 고쳤거나 못 고쳤으면 같이 알린다. 텔레그램으로 간다.
        plan["warnings"] += plan.get("figure_notes") or []
        for w in plan["warnings"]:
            print(f"  [규칙] {w}")

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


def announce(record, creds, warnings=None):
    """발행 직후 운영자에게 알린다.

    쓰레드는 올리고 10분 안에 반응이 없으면 확산이 멈춘다. 워크플로가
    조용히 올리고 끝나면 그 시간을 통째로 놓치므로, 글 주소와 함께
    지금 붙어 있어야 한다는 걸 알린다.

    주소 조회는 덤이라 실패해도 발행 자체에는 영향을 주지 않는다.
    """
    links = []
    if record.get("threads_id") and config.available(creds, "threads"):
        try:
            api = Threads(creds["threads"]["user_id"], creds["threads"]["token"])
            url = api.permalink(record["threads_id"])
            if url:
                links.append(f"쓰레드 {url}")
        except Exception:
            pass
    if record.get("ig_id") and config.available(creds, "instagram"):
        try:
            api = Instagram(creds["instagram"]["user_id"], creds["instagram"]["token"])
            url = api.permalink(record["ig_id"])
            if url:
                links.append(f"인스타 {url}")
        except Exception:
            pass

    where = []
    if record.get("threads_id"):
        where.append("쓰레드")
    if record.get("ig_id"):
        where.append("인스타")

    lines = [
        f"✅ <b>{'·'.join(where)}에 올라갔습니다</b> · {config.kind_label(record.get('kind'))}",
        "",
        "지금부터 30분이 확산을 가릅니다.",
        "댓글이 달리면 바로 답해주세요. 초기 반응이 없으면 거기서 멈춥니다.",
    ]
    if links:
        lines += [""] + links
    # 규칙에 걸린 채로 나갔으면 같이 알린다. 로그만 남기면 아무도 안 본다.
    if warnings:
        lines += ["", "글쓰기 규칙에 걸렸습니다"] + [f"- {w}" for w in warnings[:5]]
    tell_owner("\n".join(lines))
    print("발행 알림을 보냈습니다")


def publish():
    plan = store.load(PENDING, {})
    if not plan or not plan.get("texts"):
        print("발행할 내용 없음")
        return

    creds = config.credentials()
    state = store.load(STATE, {"posts": [], "queue_done": []})
    # source 를 같이 남긴다. 대기열 글인지 자동 글인지 가려야 하루 한 번
    # 잠금이 걸린다. kind 만으로는 대기열 글끼리 구분되지 않는다.
    record = {"date": store.today_kst(), "kind": plan.get("kind") or plan.get("source"),
              "source": plan.get("source")}
    if plan.get("image_rels"):
        record["images"] = plan["image_rels"]

    image_urls = plan.get("image_urls") or []
    images_ready = None
    problems = []
    paused_now = False

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
                # 인스타와 같은 이유로 Pages 배포가 끝나야 한다. 쓰레드도
                # 우리 서버에서 이미지를 내려받아 가기 때문이다.
                th_image = None
                if plan.get("threads_image") and image_urls:
                    if images_ready is None:
                        images_ready = all(wait_for_image(u) for u in image_urls)
                    if images_ready:
                        th_image = image_urls[0]
                    else:
                        # 사진 없이라도 글은 내보낸다. 글이 통째로 빠지는
                        # 것보다 낫다.
                        problems.append("쓰레드: 이미지 URL이 안 열려 글만 올립니다")
                media_id = api.publish_text(text, image_url=th_image)
                record["threads_id"] = media_id
                print(f"쓰레드 발행 완료: {media_id}" + (" (사진 포함)" if th_image else ""))

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
                # 발행 전 최신 게시물 ID. 오류가 나도 실제로 올라갔는지
                # 판별하는 기준이 된다.
                before_id = latest_media_id(api)

                try:
                    if len(image_urls) >= 2:
                        media_id = api.publish_carousel(image_urls, text)
                        print(f"인스타 캐러셀 발행 완료 ({len(image_urls)}장): {media_id}")
                    else:
                        media_id = api.publish_image(image_urls[0], text)
                        print(f"인스타 발행 완료: {media_id}")
                except Exception as e:
                    # 인스타는 게시에 성공하고도 오류 응답을 주는 경우가 있다.
                    # 실제로 올라갔는지 확인하고, 올라갔으면 성공으로 처리한다.
                    after_id = latest_media_id(api)
                    if after_id and after_id != before_id:
                        media_id = after_id
                        print(f"  오류 응답이 왔지만 실제로는 게시됨: {media_id}")
                        print(f"  (응답 내용: {str(e)[:200]})")
                    else:
                        raise
                record["ig_id"] = media_id

        except Exception as e:
            msg = f"{platform} 발행 실패: {e}"
            print(f"  ! {msg}")
            problems.append(msg)
            # 실제로도 안 올라갔고 스팸 차단(subcode 2207051)이면 며칠 쉰다.
            # 매일 재시도하며 컨테이너를 만들어대면 차단만 길어진다.
            if platform == "instagram" and is_blocked(e):
                until = (store.now_kst() + timedelta(days=PAUSE_DAYS)).strftime("%Y-%m-%d")
                state["instagram_paused_until"] = until
                paused_now = True
                problems.append(
                    f"인스타가 게시를 차단했습니다 (스팸 의심). {until}까지 인스타 발행을 멈춥니다."
                )

    if record.get("threads_id") or record.get("ig_id"):
        announce(record, creds, plan.get("warnings"))
        state.setdefault("posts", []).append(record)
        state["posts"] = state["posts"][-60:]
        # 올라간 큐 글은 여기서 done으로 찍는다. 이 표시가 없으면
        # pick_from_queue가 다음 실행에서 같은 글을 다시 집어서, 큐가
        # 첫 글에 멈춘 채 그 글만 매일 반복된다. 뒤에 쌓아둔 글은
        # 영영 나가지 않는다.
        if plan.get("source") == "manual" and plan.get("key"):
            state.setdefault("queue_done", []).append(plan["key"])
            state["queue_done"] = state["queue_done"][-200:]
        _save_state = True
    else:
        # 한 곳도 못 올렸으면 done으로 찍지 않는다. 다음 실행에서 다시
        # 시도해야 한다.
        _save_state = paused_now

    if _save_state:
        store.save(STATE, state)

    store.save(PENDING, {})

    if problems:
        tell_owner("⚠️ <b>SNS 자동 발행 문제</b>\n\n" + "\n".join(problems[:5]))


if __name__ == "__main__":
    args = sys.argv[1:]
    stage = args[0] if args and not args[0].startswith("-") else "prepare"
    only = next((a.split("=", 1)[1] for a in args if a.startswith("--only=")), None)
    brief = next((a.split("=", 1)[1] for a in args if a.startswith("--brief=")), None)
    window = next((a.split("=", 1)[1] for a in args if a.startswith("--window=")), None)
    try:
        if stage == "publish":
            publish()
        else:
            prepare(
                dry_run="--dry-run" in args,
                only=only or None,
                force="--force" in args,
                auto="--auto" in args,
                queue_only="--queue-only" in args,
                brief=brief or None,
                window=window or None,
            )
    except Exception:
        traceback.print_exc()
        tell_owner("🛑 <b>SNS 자동 발행 스크립트 오류</b>\n\n실행 로그를 확인해주세요.")
        sys.exit(1)
