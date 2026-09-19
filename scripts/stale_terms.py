#!/usr/bin/env python3
"""이름을 바꿨는데 안 따라온 글을 찾는다.

숫자가 낡는 것은 `figures.py` 가 발행 직전에 맞춘다. 그런데 **낱말이 낡는
것**은 못 잡는다. 값이 아니라 이름이라서다.

실제로 세 번 있었다.

  · 21일선을 20일선으로 바꿨는데 쓰레드 고정글이 21일선인 채로 있었다.
    화면은 20일선으로 계산하는데 글은 21일선이라고 했다.
  · 같은 것이 블로그 발행글에도 있었다(MA21 이탈 27건).
  · 서버가 5분 크론이 아니게 됐는데 오닐 탭 빈 화면이 "장중 5분마다"였다.

셋 다 사람이 우연히 보고 찾았다. 그래서 센다.

바꾼 이름을 여기 적어 두면 세션을 열 때마다 훑는다. 깨끗하면 아무것도
안 찍는다.

  python scripts/stale_terms.py      깨끗하면 조용
  python scripts/stale_terms.py -v   깨끗해도 한 줄 찍는다
"""
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]

# (찾을 것, 지금 쓰는 말, 왜)
STALE = [
    (r"21일선", "20일선", "2026-09-16 에 계산까지 20일로 바꿨다"),
    (r"MA21", "MA20", "같은 건. backtest.py TRAIL_STEPS 도 20 이다"),
    (r"21일 평균선", "20일 평균선", "같은 건"),
    (r"5분마다\s*(?:갱신|자동|감지)", "실제 갱신 시각", "고정 시각으로 바꿨다(09:10·11:10·13:10·15:35·20:05)"),
    (r"ath_cache|Stock Metadata 워크플로", "쉬운 말", "사용자 화면에 개발자 말이 새어 나간 자리"),
]

# 사용자가 읽는 것만 본다. 코드 주석과 이력은 뺀다.
TARGETS = [
    "index.html", "about/index.html", "privacy/index.html",
    "content/posts.yml", "content/고정글.txt", "content/social.yml",
]
GLOBS = ["docs/블로그글/*.txt", "docs/*.md", "docs/*.txt"]

# 여기 파일들은 "왜 바꿨는지" 적는 곳이라 옛 이름이 나오는 게 정상이다.
SKIP = {"docs/작업일지.md"}


def files():
    seen = []
    for t in TARGETS:
        p = ROOT / t
        if p.exists():
            seen.append(p)
    for g in GLOBS:
        seen += sorted(ROOT.glob(g))
    out, done = [], set()
    for p in seen:
        rel = p.relative_to(ROOT).as_posix()
        if rel in SKIP or rel in done:
            continue
        done.add(rel)
        out.append(p)
    return out


def main():
    hits = []
    for p in files():
        try:
            text = p.read_text(encoding="utf-8-sig")
        except Exception:
            continue
        for i, line in enumerate(text.split("\n"), 1):
            s = line.strip()
            if not s or s.startswith("#") or s.lstrip().startswith("//"):
                continue
            for pat, now, why in STALE:
                if re.search(pat, s):
                    hits.append((p.relative_to(ROOT).as_posix(), i, s[:70], now, why))
                    break

    if not hits:
        if "-v" in sys.argv:
            print("낡은 낱말 검사: 이상 없음")
        return 0

    print("낡은 낱말 %d곳 — 이름을 바꿨는데 안 따라온 자리입니다" % len(hits))
    for path, n, s, now, why in hits[:15]:
        print("  %s:%d  → %s" % (path, n, now))
        print("      %s" % s)
        print("      (%s)" % why)
    if len(hits) > 15:
        print("  … 외 %d곳" % (len(hits) - 15))
    return 1


if __name__ == "__main__":
    sys.exit(main())
