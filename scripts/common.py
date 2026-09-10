#!/usr/bin/env python3
"""
여러 스크립트가 같이 쓰는 것들.

같은 코드가 파일마다 복사돼 있으면 한 곳을 고쳐도 나머지가 안 고쳐진다.
특히 mask()는 공개 Actions 로그에 개인 식별자가 남는 걸 막는 함수라,
사본이 흩어져 있으면 규칙을 바꿀 때 하나를 놓치기 쉽다.

스크립트는 전부 `python scripts/무엇.py`로 실행되므로 scripts/가
sys.path에 들어간다. social/ 패키지에서도 `import common`이 그대로 된다.
"""
import json
from pathlib import Path

import requests

# ---------------------------------------------------------------
# 텔레그램
# ---------------------------------------------------------------

TELEGRAM_API = "https://api.telegram.org"


def mask(value):
    """공개 로그에 그대로 남지 않도록 뒤 3자리만 남긴다."""
    s = str(value)
    return "***" + s[-3:] if len(s) > 3 else "***"


def send_message(
    token,
    chat_id,
    text,
    parse_mode="HTML",
    disable_preview=True,
    timeout=20,
):
    """텔레그램 메시지 발송. (성공여부, 설명)을 돌려준다.

    HTTP 200이어도 텔레그램이 ok=false를 주는 경우가 있어서 본문까지 본다.
    설명 문자열은 호출한 쪽이 로그에 그대로 찍으므로 chat_id 같은 식별자를
    담지 않는다.
    """
    payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    if disable_preview:
        payload["disable_web_page_preview"] = True
    try:
        r = requests.post(
            f"{TELEGRAM_API}/bot{token}/sendMessage", json=payload, timeout=timeout
        )
        try:
            body = r.json()
        except Exception:
            body = {}
        if r.status_code == 200 and body.get("ok", False):
            return True, "OK"
        err = body.get("description") or r.text[:200]
        return False, f"HTTP {r.status_code}: {err}"
    except Exception as e:
        return False, f"Exception: {e}"


# ---------------------------------------------------------------
# JSON 파일
# ---------------------------------------------------------------


def load_json(path, default=None):
    """JSON 파일을 읽는다. 없거나 깨졌으면 default를 돌려준다."""
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path, data, indent=2, sort_keys=False):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent, sort_keys=sort_keys)
    return p


# ---------------------------------------------------------------
# data/ 읽기
# ---------------------------------------------------------------

HISTORY_DIR = Path("data/history")
DART_FILE = Path("data/dart_financials.json")


def load_recent_history(days=10):
    """data/history/{date}.json 최근 N일. 최신이 앞이다.

    각 항목에 파일 이름을 _date로 넣어준다.
    """
    if not HISTORY_DIR.exists():
        return []
    files = sorted(
        [f for f in HISTORY_DIR.glob("*.json") if f.stem != "index"],
        reverse=True,
    )[:days]
    out = []
    for f in files:
        data = load_json(f)
        if data is None:
            continue
        data["_date"] = f.stem
        out.append(data)
    return out


def load_dart_financials():
    """data/dart_financials.json 캐시."""
    return load_json(DART_FILE, {})


# ---------------------------------------------------------------
# 네이버 테마
# ---------------------------------------------------------------


def fetch_theme_members(theme_no, headers=None, timeout=8):
    """테마 하나에 속한 종목 코드 목록. 실패하면 빈 목록.

    User-Agent는 호출하는 쪽마다 달라서 headers를 그대로 받는다.
    """
    url = f"https://m.stock.naver.com/api/stocks/theme/{theme_no}?page=1&pageSize=50"
    head = {**(headers or {}), "Referer": "https://m.stock.naver.com/"}
    try:
        r = requests.get(url, headers=head, timeout=timeout)
        if r.status_code != 200:
            return []
        data = r.json()
        return [
            str(s.get("itemCode")).zfill(6)
            for s in data.get("stocks", [])
            if s.get("itemCode")
        ]
    except Exception:
        return []


# ---------------------------------------------------------------
# Supabase
# ---------------------------------------------------------------


def supabase_headers(service_key):
    return {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
    }


def supabase_get(base_url, headers, table, params, timeout=30):
    r = requests.get(
        f"{base_url}/rest/v1/{table}", headers=headers, params=params, timeout=timeout
    )
    r.raise_for_status()
    return r.json()
