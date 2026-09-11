#!/usr/bin/env python3
"""
댓글 자동 대응.

최근에 올린 글의 댓글을 훑어서
  1) 키워드 규칙에 맞는 답장을 달고
  2) 인스타는 그 댓글 작성자에게 비공개 답장(DM)으로 링크를 보낸다.

인스타 DM은 '댓글에 대한 비공개 답장'만 사용한다.
  - 댓글 1건당 딱 1회, 댓글 작성 후 7일 이내에만 가능
  - 댓글을 달지 않은 사람에게는 보내지 않는다 (정책 위반이자 계정 정지 사유)
쓰레드에는 DM API가 없어서 링크를 공개 답글에 담아 보낸다.

운영자가 손으로 먼저 답글을 단 댓글에는 봇이 또 달지 않는다.
handled.json에 없더라도 실제 댓글창을 보고 내 답글이 있으면 건너뛴다.

처리한 댓글은 data/social/handled.json에 ID만 기록한다.
저장소가 Public이므로 작성자 아이디나 댓글 본문은 남기지 않는다.

사용:
  python scripts/social_engage.py
  python scripts/social_engage.py --dry-run
"""
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from social import config, matcher, store  # noqa: E402
from social.instagram_api import Instagram  # noqa: E402
from social.notify import tell_owner  # noqa: E402
from social.threads_api import Threads  # noqa: E402

HANDLED = "handled"

# 인스타 비공개 답장은 댓글 작성 후 7일까지만 허용된다. 여유를 두고 6일로 자른다.
DM_WINDOW_DAYS = 6


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


def age_days(ts):
    if not ts:
        return 999
    return (datetime.now(timezone.utc) - ts).total_seconds() / 86400


class Runner:
    def __init__(self, cfg, dry_run=False):
        self.cfg = cfg
        self.engage = cfg.get("engage") or {}
        link_cfg = cfg.get("link") or {}
        self.link = link_cfg.get("url", "")
        self.brand = link_cfg.get("label", "")
        self.source_param = (link_cfg.get("source_param") or "").strip()
        self.dry_run = dry_run
        self.handled = store.load(HANDLED, {"replied": {}, "dm": {}})
        self.handled.setdefault("replied", {})
        self.handled.setdefault("dm", {})
        self.budget = int(self.engage.get("max_actions_per_run") or 20)
        self.log = []
        self.problems = []
        # 이미 답글이 달려 있어 건너뛴 댓글 수. 공개 로그라 아이디는 남기지 않는다.
        self.already = 0
        # 쓰레드는 DM API가 없다. 링크를 원한 사람은 여기 모아뒀다가
        # 운영자에게 텔레그램으로 알려서 직접 보내게 한다.
        self.manual_dm = []

    def link_for(self, platform):
        """플랫폼 꼬리표를 붙인 링크.

        같은 주소를 보내면 방문자가 쓰레드에서 왔는지 인스타에서 왔는지
        알 수 없다. 꼬리표를 붙여야 어느 쪽이 사람을 데려오는지 보인다.
        """
        if not (self.link and self.source_param):
            return self.link
        sep = "&" if "?" in self.link else "?"
        return f"{self.link}{sep}{self.source_param}={platform}"

    def spent(self):
        return self.budget <= 0

    def mark(self, bucket, key):
        self.handled[bucket][key] = store.today_kst()

    def done(self, bucket, key):
        return key in self.handled[bucket]

    def skip_already(self, *keys):
        """이미 답글이 달린 댓글. 다시 보지 않도록 닫아둔다."""
        for bucket, key in keys:
            self.mark(bucket, key)
        self.already += 1

    # --- 쓰레드 ---

    def threads_answered(self, api, post_id, me):
        """이 글에서 내가 이미 답글을 단 댓글 ID 모음.

        운영자가 손으로 먼저 답글을 달았는데 봇이 또 달면 댓글이 겹친다.
        /replies는 최상위 답글만 주므로 댓글 아래 달린 내 답글은 보이지 않는다.
        대화 전체를 주는 /conversation을 읽어 replied_to로 부모를 찾는다.
        """
        try:
            convo = api.conversation(post_id)
        except Exception as e:
            # 대화를 못 읽으면 중복 여부를 알 수 없다.
            # 겹친 댓글보다 빠진 답글이 낫다고 보고 이 글은 통째로 건너뛴다.
            self.problems.append(f"쓰레드 대화 조회 실패 {post_id}: {e}")
            return None

        answered = set()
        for item in convo:
            if (item.get("username") or "") != me:
                continue
            parent = (item.get("replied_to") or {}).get("id")
            if parent:
                answered.add(str(parent))
        return answered

    def run_threads(self, creds):
        api = Threads(creds["user_id"], creds["token"])
        me = api.username()
        since = (datetime.now(timezone.utc) - timedelta(
            days=int(self.engage.get("lookback_days") or 7)
        )).timestamp()

        posts = api.recent_posts(since_ts=since)
        print(f"[쓰레드] 최근 글 {len(posts)}개 · 계정 @{me}")

        for post in posts:
            if self.spent():
                break
            try:
                replies = api.replies(post["id"])
            except Exception as e:
                self.problems.append(f"쓰레드 답글 조회 실패 {post['id']}: {e}")
                continue

            answered = None  # 답할 댓글이 있을 때만 대화를 읽는다

            for reply in replies:
                if self.spent():
                    break
                author = reply.get("username") or ""
                if author == me:
                    continue
                if reply.get("hide_status") == "HIDDEN":
                    continue

                key = f"threads:{reply['id']}"
                if self.done("replied", key):
                    continue

                if answered is None:
                    answered = self.threads_answered(api, post["id"], me)
                    if answered is None:
                        break  # 대화를 못 읽었다 - 이 글은 다음 실행에서
                if str(reply["id"]) in answered:
                    # 운영자가 손으로 먼저 답글을 달았다. 겹쳐 달지 않는다.
                    self.skip_already(("replied", key))
                    continue

                hit = matcher.match(reply.get("text"), self.engage)
                if not hit:
                    self.mark("replied", key)  # 규칙 없음 - 다시 보지 않음
                    continue
                name, rule = hit

                text = matcher.fill(
                    matcher.pick(rule, "reply", "threads"),
                    user=author, link=self.link_for("threads"), brand=self.brand,
                )
                if not text:
                    self.mark("replied", key)
                    continue

                # 이 규칙이 DM을 보내도록 돼 있으면, 쓰레드에서는 대신
                # 운영자가 직접 보내야 하므로 목록에 담아둔다
                needs_dm = bool(matcher.pick(rule, "dm", "threads"))

                if self.dry_run:
                    print(f"  [예행] @{author} <{name}> -> {text[:60]}")
                    if needs_dm:
                        print(f"         └ 직접 DM 대상: @{author}")
                else:
                    try:
                        api.publish_text(text[:500], reply_to_id=reply["id"])
                        answered.add(str(reply["id"]))
                        self.mark("replied", key)
                        self.budget -= 1
                        self.log.append(f"쓰레드 답글 @{author} ({name})")
                        if needs_dm:
                            self.manual_dm.append((author, name))
                        time.sleep(1)
                    except Exception as e:
                        self.problems.append(f"쓰레드 답글 실패 @{author}: {e}")

    # --- 인스타그램 ---

    @staticmethod
    def ig_answered(comment, me):
        """이 댓글에 내 계정이 이미 답글을 달았는지.

        인스타는 댓글을 조회할 때 replies를 같이 주므로 따로 부르지 않아도 된다.
        """
        for r in ((comment.get("replies") or {}).get("data") or []):
            who = r.get("username") or (r.get("from") or {}).get("username") or ""
            if who == me:
                return True
        return False

    def run_instagram(self, creds):
        api = Instagram(creds["user_id"], creds["token"])
        me = api.username()
        lookback = int(self.engage.get("lookback_days") or 7)

        media = api.recent_media()
        media = [m for m in media if age_days(parse_ts(m.get("timestamp"))) <= lookback]
        print(f"[인스타] 최근 글 {len(media)}개 · 계정 @{me}")

        for post in media:
            if self.spent():
                break
            try:
                comments = api.comments(post["id"])
            except Exception as e:
                self.problems.append(f"인스타 댓글 조회 실패 {post['id']}: {e}")
                continue

            for c in comments:
                if self.spent():
                    break
                author = c.get("username") or (c.get("from") or {}).get("username") or ""
                if author == me:
                    continue

                key = f"ig:{c['id']}"
                if self.done("replied", key) and self.done("dm", key):
                    continue

                # 운영자가 손으로 먼저 답글을 달았으면 이 댓글은 건드리지 않는다.
                # 답글이 겹치는 것도 문제지만, 사람이 이미 응대한 댓글에
                # 봇 DM까지 따라 나가면 두 번 받는 꼴이 된다.
                if self.ig_answered(c, me):
                    self.skip_already(("replied", key), ("dm", key))
                    continue

                hit = matcher.match(c.get("text"), self.engage)
                if not hit:
                    self.mark("replied", key)
                    self.mark("dm", key)
                    continue
                name, rule = hit
                fresh = age_days(parse_ts(c.get("timestamp"))) <= DM_WINDOW_DAYS

                # 1) 비공개 답장(DM)을 먼저 보낸다. 댓글당 1회, 7일 이내.
                #    답글 문구가 "DM 보내드렸어요"인데 DM이 실패하면 거짓말이 되므로
                #    DM 결과를 먼저 확정하고 그에 맞는 답글을 고른다.
                dm = matcher.fill(
                    matcher.pick(rule, "dm", "instagram"),
                    user=author, link=self.link_for("instagram"), brand=self.brand,
                )
                dm_ok = True

                if dm and not self.done("dm", key):
                    if not fresh:
                        print(f"  DM 창 만료 (7일 초과) @{author}")
                        dm_ok = False
                        self.mark("dm", key)
                    elif self.dry_run:
                        print(f"  [예행] DM @{author} <{name}> -> {dm[:60]}")
                    else:
                        try:
                            api.private_reply(c["id"], dm[:1000])
                            self.mark("dm", key)
                            self.budget -= 1
                            self.log.append(f"인스타 DM @{author} ({name})")
                            time.sleep(1)
                        except Exception as e:
                            # 권한 부족이거나 이미 보낸 댓글. 재시도해도 같으므로 닫는다.
                            self.problems.append(f"인스타 DM 실패 @{author}: {e}")
                            dm_ok = False
                            self.mark("dm", key)
                elif dm:
                    dm_ok = self.done("dm", key)
                else:
                    self.mark("dm", key)

                # 2) 공개 답글. DM이 나가지 못했으면 링크가 담긴 대체 문구를 쓴다.
                if not self.done("replied", key):
                    field = "reply" if (dm_ok or not dm) else "reply_fallback"
                    text = matcher.fill(
                        matcher.pick(rule, field, "instagram")
                        or matcher.pick(rule, "reply_fallback", "instagram")
                        or matcher.pick(rule, "reply", "threads"),
                        user=author, link=self.link_for("instagram"), brand=self.brand,
                    )
                    if not text:
                        self.mark("replied", key)
                    elif self.dry_run:
                        print(f"  [예행] 답글 @{author} <{name}> -> {text[:60]}")
                    else:
                        try:
                            api.reply_to_comment(c["id"], text[:2200])
                            self.mark("replied", key)
                            self.budget -= 1
                            self.log.append(f"인스타 답글 @{author} ({name})")
                            time.sleep(1)
                        except Exception as e:
                            self.problems.append(f"인스타 답글 실패 @{author}: {e}")

    # --- 마무리 ---

    def finish(self):
        if not self.dry_run:
            self.handled["replied"] = store.prune_handled(self.handled["replied"])
            self.handled["dm"] = store.prune_handled(self.handled["dm"])
            store.save(HANDLED, self.handled)

        if self.log:
            print(f"\n처리 {len(self.log)}건")
            for line in self.log:
                print(f"  - {line}")
        else:
            print("\n새로 처리한 댓글 없음")

        if self.already:
            print(f"이미 답글이 달려 있어 건너뜀 {self.already}건")

        # 쓰레드는 DM API가 없어 봇이 링크를 못 보낸다.
        # 답글로 "디엠 드릴게요"라고 해뒀으니 실제 발송은 운영자 몫이다.
        if self.manual_dm:
            print(f"\n직접 DM 보내야 할 사람 {len(self.manual_dm)}명")
            for author, name in self.manual_dm:
                print(f"  - @{author} ({name})")
            if not self.dry_run:
                lines = [f"📩 <b>쓰레드에서 링크 요청 {len(self.manual_dm)}건</b>\n"]
                for author, name in self.manual_dm:
                    lines.append(f"@{author}  <i>{name}</i>")
                lines.append("\n답글로 '디엠 드릴게요'라고 해뒀습니다.")
                lines.append("쓰레드는 DM API가 없어 직접 보내주셔야 합니다.")
                tell_owner("\n".join(lines))

        if self.problems:
            print(f"\n문제 {len(self.problems)}건")
            for p in self.problems:
                print(f"  ! {p}")
            tell_owner("⚠️ <b>SNS 댓글 대응 문제</b>\n\n" + "\n".join(self.problems[:5]))


def main(dry_run=False):
    cfg = config.load_config()
    if not (cfg.get("engage") or {}).get("enabled", True):
        print("댓글 대응 꺼짐 (engage.enabled=false)")
        return

    creds = config.credentials()
    runner = Runner(cfg, dry_run=dry_run)

    if config.available(creds, "threads"):
        try:
            runner.run_threads(creds["threads"])
        except Exception as e:
            runner.problems.append(f"쓰레드 처리 중단: {e}")
    else:
        print("[쓰레드] 자격증명 없음 - 건너뜀")

    if config.available(creds, "instagram"):
        try:
            runner.run_instagram(creds["instagram"])
        except Exception as e:
            runner.problems.append(f"인스타 처리 중단: {e}")
    else:
        print("[인스타] 자격증명 없음 - 건너뜀")

    runner.finish()


if __name__ == "__main__":
    try:
        main(dry_run="--dry-run" in sys.argv[1:])
    except Exception:
        traceback.print_exc()
        tell_owner("🛑 <b>SNS 댓글 대응 스크립트 오류</b>\n\n실행 로그를 확인해주세요.")
        sys.exit(1)
