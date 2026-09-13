#!/usr/bin/env python3
"""이 세션이 어느 Chrome 에 붙어 있는지 확인한다.

.mcp.json 의 CHROME_PORT 가 세션 밖에서 자꾸 바뀐다. 오늘만 12316 에서
12317 로, 다시 12318 로 두 번 바뀌었다. 세션은 시작할 때 값을 한 번만
읽으므로, 도중에 파일이 바뀌어도 세션은 예전 포트에 붙어 있다. 그래서
"설정은 12316 인데 실제로는 12318 을 보고 있는" 상태가 생긴다.

파일에 적힌 값과 실제로 붙은 포트를 따로 재서 어긋나면 알려 준다.

실제 포트를 알아내는 방법:
  브리지마다 자기 profile 의 탭만 본다. 포트별로 탭 목록을 받아
  서로 견주면 어느 것이 어느 profile 인지 갈린다. 탭 ID 는 Chrome 안에서
  유일하므로 겹치지 않는다.

사용:
  python scripts/chrome_port_check.py
  python scripts/chrome_port_check.py --want 12316
"""
import argparse
import json
import re
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
PORTS = [12315, 12316, 12317, 12318, 12319]
H = {"Content-Type": "application/json",
     "Accept": "application/json, text/event-stream"}


def _parse(resp):
    text = resp.text
    if "data:" not in text:
        return resp.json()
    best = None
    for line in text.splitlines():
        if line.startswith("data:"):
            try:
                obj = json.loads(line[5:].strip())
            except Exception:
                continue
            if "result" in obj or "error" in obj:
                best = obj
    return best or {}


def probe(port, timeout=6):
    """포트가 살아 있으면 탭 목록을 돌려준다. 아니면 None."""
    url = f"http://127.0.0.1:{port}/mcp"
    try:
        s = requests.Session()
        r = s.post(url, headers=H, timeout=timeout, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "port-check", "version": "1"}}})
        h = dict(H)
        sid = r.headers.get("mcp-session-id")
        if sid:
            h["mcp-session-id"] = sid
        s.post(url, headers=h, timeout=timeout,
               json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        r2 = s.post(url, headers=h, timeout=timeout + 8, json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "get_windows_and_tabs", "arguments": {}}})
        info = json.loads(_parse(r2)["result"]["content"][0]["text"])
        return [{"id": t["tabId"], "url": t["url"]}
                for w in info["windows"] for t in w["tabs"]]
    except Exception:
        return None


def configured_port():
    """.mcp.json 에 적힌 값. 환경변수가 있으면 그쪽이 이긴다."""
    import os
    env = os.environ.get("CHROME_PORT", "").strip()
    try:
        raw = (ROOT / ".mcp.json").read_text(encoding="utf-8")
        m = re.search(r'"CHROME_PORT"\s*:\s*"([^"]+)"', raw)
        spec = m.group(1) if m else ""
    except Exception:
        spec = ""
    # 파일이 "${CHROME_PORT:-12316}" 이면 환경변수가 이긴다.
    # 그냥 "12317" 이면 환경변수를 무시하고 그 값이 쓰인다. 이 차이를
    # 놓치면 "환경변수를 줬으니 괜찮다"고 잘못 판단한다. 실제로 그랬다.
    m = re.search(r":-(\d+)", spec)
    if m:
        default = m.group(1)
        applied = env or default
        overridable = True
    elif spec.isdigit():
        default = spec
        applied = spec          # 하드코딩 — 환경변수는 무시된다
        overridable = False
    else:
        default = ""
        applied = env or ""
        overridable = True
    return {"환경변수": env or None, "파일기본값": default or None,
            "적용값": applied or None, "파일표기": spec,
            "환경변수우선": overridable}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--want", default="12316", help="이 세션이 써야 하는 포트")
    args = ap.parse_args()

    cfg = configured_port()
    print("설정")
    print(f"  .mcp.json 표기 : {cfg['파일표기']}")
    print(f"  환경변수       : {cfg['환경변수'] or '(없음)'}")
    print(f"  적용될 값      : {cfg['적용값'] or '(없음)'}")
    if not cfg["환경변수우선"]:
        print("  ※ 파일에 포트가 하드코딩돼 있어 환경변수가 무시됩니다.")

    print("\n살아 있는 브리지")
    alive = {}
    for p in PORTS:
        tabs = probe(p)
        if tabs is None:
            continue
        alive[p] = tabs
        head = tabs[0]["url"][:55] if tabs else "(탭 없음)"
        print(f"  {p}: 탭 {len(tabs)}개  {head}")
    if not alive:
        print("  없음 — Chrome 확장에서 '강제 재연결'을 눌러 주세요")
        return 1

    want = args.want
    problems = []
    if cfg["적용값"] != want:
        problems.append(
            f"설정이 {cfg['적용값']} 입니다. 이 세션은 {want} 를 써야 합니다.")
    if int(want) not in alive:
        problems.append(f"{want} 브리지가 죽어 있습니다.")

    print()
    if problems:
        print("★ 문제")
        for p in problems:
            print(f"  - {p}")
        print(f"\n  고치려면: 세션을 끝내고  CHROME_PORT={want} claude  로 다시 시작하세요.")
        print("  환경변수는 .mcp.json 기본값보다 우선하므로 파일이 또 바뀌어도 흔들리지 않습니다.")
        return 1

    print(f"정상 — 설정과 브리지 모두 {want} 입니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
