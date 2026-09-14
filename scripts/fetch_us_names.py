#!/usr/bin/env python3
"""미국 종목의 한글 이름을 받아 둔다.

미장 브리핑에 'Occidental Petroleum' 이라고 적으면 40~70대 독자는 그게
무슨 회사인지 모른다. 네이버 증권은 한글 이름을 갖고 있으므로 받아서
data/us_names_ko.json 에 쌓아 둔다.

**글을 쓸 때 받아오지 않는다.** 브리핑은 아침 8시에 한 번 도는데, 거기서
종목마다 네트워크를 타면 느리고 한 번 실패하면 그날 글이 어긋난다. 이름은
거의 안 바뀌므로 미리 받아 두고 파일에서 읽는다.

이미 받은 것은 다시 묻지 않는다. 처음 한 번만 오래 걸리고 그다음부터는
새로 생긴 종목만 받는다.

**티커가 정확히 일치하는 항목만 쓴다.** 자동완성은 비슷한 것도 같이
돌려준다. COP 를 물으면 CPRT·CPA·CDP 가 따라온다. 앞에서 첫 번째를
집으면 엉뚱한 회사 이름이 박힌다.

사용:
  python scripts/fetch_us_names.py           없는 것만
  python scripts/fetch_us_names.py --all     전부 다시
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "us_names_ko.json"
AC = "https://ac.stock.naver.com/ac"
HEADERS = {"Referer": "https://m.stock.naver.com/", "User-Agent": "Mozilla/5.0"}

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")


def load(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def tickers():
    """받아야 할 티커 목록. 유니버스와 최근 결과를 합친다."""
    out = set(load(ROOT / "data" / "us_universe.json", {}))
    res = load(ROOT / "data" / "us_results.json", {})
    for key in ("minervini", "momentum", "oneil"):
        for r in (res.get(key) or {}).get("results") or []:
            if r.get("code"):
                out.add(r["code"])
    return sorted(out)


def korean_name(ticker):
    """한글 이름. 없으면 None.

    네이버가 한글 이름을 안 가진 종목은 영문 이름을 그대로 돌려준다.
    그런 건 저장해 봐야 화면이 달라지지 않으므로 거른다.
    """
    try:
        r = requests.get(AC, params={"q": ticker, "target": "stock",
                                     "country": "USA"},
                         headers=HEADERS, timeout=10)
        items = r.json().get("items") or []
    except Exception:
        return None
    for it in items:
        if not isinstance(it, dict) or it.get("code") != ticker:
            continue
        name = (it.get("name") or "").strip()
        # 한글이 한 글자도 없으면 영문 이름을 돌려준 것이다.
        return name if re.search(r"[가-힣]", name) else None
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="이미 받은 것도 다시")
    args = ap.parse_args()

    have = load(OUT, {})
    todo = [t for t in tickers() if args.all or t not in have]
    print(f"전체 {len(tickers())}개 · 받을 것 {len(todo)}개")

    got = 0
    for i, t in enumerate(todo, 1):
        name = korean_name(t)
        if name:
            have[t] = name
            got += 1
        else:
            # 못 찾은 것도 적어 둔다. 안 그러면 매번 다시 물어본다.
            have.setdefault(t, None)
        if i % 50 == 0:
            print(f"  {i}/{len(todo)} ...")
        time.sleep(0.2)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(have, ensure_ascii=False, indent=1, sort_keys=True),
                   encoding="utf-8")
    named = sum(1 for v in have.values() if v)
    print(f"저장: data/us_names_ko.json · 한글 이름 {named}개 / 전체 {len(have)}개")
    for t in list(todo)[:5]:
        print(f"  {t} -> {have.get(t)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
