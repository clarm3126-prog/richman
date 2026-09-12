#!/usr/bin/env python3
"""
설정 로딩.

content/social.yml에서 규칙을 읽고, 토큰류는 환경변수에서 읽는다.
설정 파일은 저장소에 공개되므로 비밀값은 절대 넣지 않는다.
"""
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = ROOT / "content" / "social.yml"
POSTS_FILE = ROOT / "content" / "posts.yml"

# 저장소에 공개 배포되는 주소 (인스타 이미지 호스팅에 사용)
PAGES_BASE = "https://clarm3126-prog.github.io/richman"

THREADS_API = "https://graph.threads.net/v1.0"
THREADS_AUTH = "https://graph.threads.net"
IG_API = "https://graph.instagram.com/v25.0"
IG_AUTH = "https://graph.instagram.com"


# 글 형식의 읽기 좋은 이름. 성과 요약과 발행 알림이 같이 쓴다.
# 여기 없는 이름은 kind를 그대로 보여준다.
KIND_LABELS = {
    "promo": "무료 배포",
    "trade_log": "매매 기록",
    "lesson": "정리·원칙",
    "screener": "스크리너",
    "momentum": "모멘텀",
    "theme": "테마",
    "guide": "사용 안내",
    "manual": "직접 쓴 글",
}


def kind_label(kind):
    return KIND_LABELS.get(kind, kind or "?")


def env(name, default=""):
    return os.environ.get(name, default).strip()


def load_config():
    if not CONFIG_FILE.exists():
        raise SystemExit(f"설정 파일 없음: {CONFIG_FILE}")
    with CONFIG_FILE.open(encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    cfg.setdefault("link", {})
    cfg.setdefault("post", {})
    cfg.setdefault("engage", {})
    return cfg


def load_post_queue():
    """수동 작성 글 큐. 없으면 빈 리스트."""
    if not POSTS_FILE.exists():
        return []
    with POSTS_FILE.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("queue") or []


def credentials():
    """플랫폼별 자격증명. 없으면 그 플랫폼은 건너뛴다."""
    return {
        "threads": {
            "user_id": env("THREADS_USER_ID"),
            "token": env("THREADS_ACCESS_TOKEN"),
        },
        "instagram": {
            "user_id": env("IG_USER_ID"),
            "token": env("IG_ACCESS_TOKEN"),
        },
    }


def available(creds, platform):
    c = creds.get(platform) or {}
    return bool(c.get("user_id") and c.get("token"))
