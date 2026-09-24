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
#
# **이름이 바뀐 것만 넣는다.** 어디에 나오든 무조건 틀린 것이라야 한다.
#
# "5분마다 갱신" 과 "ath_cache" 도 넣어 봤다가 뺐다. 둘 다 한 번 고친
# 문구일 뿐 이름이 바뀐 게 아니어서, 맥락에 따라 맞기도 하다. 실제로
# 「왜 실시간이 아닌가」 글은 **"5분마다라고 적어놨는데 틀렸다"** 를 두 번
# 인용하는데 검사기가 그걸 고칠 자리로 잡았다. 고친 자취를 고치라고
# 하는 셈이다.
#
# 헛걸리는 경고는 결국 안 읽힌다. 확실한 것만 남긴다.
STALE = [
    (r"21일선", "20일선", "2026-09-16 에 계산까지 20일로 바꿨다"),
    (r"MA21", "MA20", "같은 건. backtest.py TRAIL_STEPS 도 20 이다"),
    (r"21일 평균선", "20일 평균선", "같은 건"),
]

# 사용자가 읽는 것만 본다. 코드 주석과 이력은 뺀다.
TARGETS = [
    "index.html", "about/index.html", "privacy/index.html",
    "content/posts.yml", "content/고정글.txt", "content/social.yml",
]
GLOBS = ["docs/블로그글/*.txt", "docs/*.md", "docs/*.txt"]

# 키트 정본은 저장소 밖에 있다. 원고를 그쪽에서 쓰므로 같이 본다.
KIT = Path("C:/블로그자동화-실전키트")
KIT_GLOBS = ["posts/*.md", "기준문서/*.md", "CLAUDE.md"]

# 여기 파일들은 "왜 바꿨는지" 적는 곳이라 옛 이름이 나오는 게 정상이다.
# 지표용어 색인은 12318 이 만든 표다. 어느 발행글이 어떤 지표 이름을 쓰는지
# 적어 두는 곳이라, 바꾼 이름이 양쪽 다 나온다. 고칠 자리가 아니라 자취다.
SKIP = {"docs/작업일지.md", "키트/posts/_발행본_지표용어_색인.md"}


def files():
    seen = []
    for t in TARGETS:
        p = ROOT / t
        if p.exists():
            seen.append(p)
    for g in GLOBS:
        seen += sorted(ROOT.glob(g))
    if KIT.exists():
        for g in KIT_GLOBS:
            seen += sorted(KIT.glob(g))

    out, done = [], set()
    for p in seen:
        try:
            rel = p.relative_to(ROOT).as_posix()
        except ValueError:
            rel = "키트/" + p.relative_to(KIT).as_posix()
        # 접은 원고는 옛 낱말이 남아 있는 게 정상이다
        if p.name.startswith("_폐기_"):
            continue
        if rel in SKIP or rel in done:
            continue
        done.add(rel)
        out.append((p, rel))
    return out


def main():
    hits = []
    unread = []
    for p, rel in files():
        try:
            text = p.read_text(encoding="utf-8-sig")
        except Exception as e:
            # **못 읽은 것을 깨끗한 것으로 말하지 않는다.** 조용히 건너뛰면
            # 그 파일에 낡은 낱말이 있어도 "이상 없음"이 뜬다.
            unread.append((rel, type(e).__name__))
            continue
        lines = text.split("\n")
        # 원고 앞머리(--- 사이)는 "전에는 이렇게 썼다" 식 기록이 들어간다.
        # 고쳐야 할 자리가 아니라 고친 자취다.
        head_end = 0
        if lines and lines[0].strip() == "---":
            for n, l in enumerate(lines[1:], 2):
                if l.strip() == "---":
                    head_end = n
                    break
        for i, line in enumerate(lines, 1):
            if i <= head_end:
                continue
            s = line.strip()
            if not s or s.startswith("#") or s.lstrip().startswith("//"):
                continue
            for pat, now, why in STALE:
                if re.search(pat, s):
                    hits.append((rel, i, s[:70], now, why))
                    break

    if unread:
        print("못 읽은 파일 %d개 — 이 파일들은 검사하지 못했습니다" % len(unread))
        for rel, why in unread:
            print("  %s  (%s)" % (rel, why))
    if not hits:
        if "-v" in sys.argv and not unread:
            print("낡은 낱말 검사: 이상 없음")
        return 1 if unread else 0

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
