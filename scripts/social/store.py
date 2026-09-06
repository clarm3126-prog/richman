#!/usr/bin/env python3
"""
상태 저장.

저장소가 Public이므로 개인정보는 남기지 않는다.
처리 여부 판단에 필요한 ID와 날짜만 기록하고,
댓글 작성자 아이디나 댓글 본문은 저장하지 않는다.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATE_DIR = ROOT / "data" / "social"

KST = timezone(timedelta(hours=9))


def now_kst():
    return datetime.now(KST)


def today_kst():
    return now_kst().strftime("%Y-%m-%d")


def _path(name):
    return STATE_DIR / f"{name}.json"


def load(name, default=None):
    p = _path(name)
    if not p.exists():
        return default if default is not None else {}
    try:
        with p.open(encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def save(name, data):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with _path(name).open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)


def prune_handled(handled, days=30):
    """오래된 처리 기록을 지워 파일이 무한정 커지지 않게 한다."""
    cutoff = (now_kst() - timedelta(days=days)).strftime("%Y-%m-%d")
    return {k: v for k, v in handled.items() if v >= cutoff}
