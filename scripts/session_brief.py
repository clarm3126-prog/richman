#!/usr/bin/env python3
"""세션을 열 때 "그동안 뭐가 바뀌었나"를 한 번 보여준다.

이 저장소는 세션 셋이 동시에 만진다. 서로 뭘 했는지 모르면 같은 자리를
두 번 고치거나, 한쪽이 방금 바꾼 것을 모르고 되돌린다. 실제로 2026-09-17
에 이런 일이 있었다.

  · 옆 세션이 아직 커밋 안 한 남의 작업을 자기 커밋에 쓸어 담았다
  · 20일선 변경을 모르고 원고를 다시 뽑아 숫자가 어긋났다
  · 발행 크론을 양쪽이 각각 건드렸다

셋 다 세션을 열 때 이 화면만 봤으면 막혔을 일이다.

기준을 "마지막으로 본 지점"이 아니라 **지난 24시간**으로 잡는다. 세션이
셋이라 마지막 지점을 파일에 적어 두면 먼저 연 세션이 그걸 지워 버려서,
나중에 연 세션은 아무것도 못 본다.
"""
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
HOURS = 24
MAX_COMMITS = 12
MAX_FILES = 6


def git(*args):
    try:
        out = subprocess.run(["git"] + list(args), cwd=ROOT, capture_output=True, timeout=15)
        return out.stdout.decode("utf-8", "replace").strip()
    except Exception:
        return ""


def main():
    if not git("rev-parse", "--is-inside-work-tree"):
        return

    lines = []

    # 1. 지난 하루 커밋. 자동 커밋([auto])은 사람이 볼 것이 없어 뺀다.
    log = git("log", "--since=%d hours ago" % HOURS, "--format=%h %s", "--no-merges")
    commits = [l for l in log.split("\n") if l.strip() and "[auto]" not in l]
    if commits:
        lines.append("지난 %d시간 커밋 %d개" % (HOURS, len(commits)))
        for c in commits[:MAX_COMMITS]:
            lines.append("  " + c)
        if len(commits) > MAX_COMMITS:
            lines.append("  … 외 %d개" % (len(commits) - MAX_COMMITS))

    # 2. 커밋 안 된 변경. 다른 세션이 작업 중일 수 있으니 쓸어 담지 않게 경고한다.
    dirty = [l for l in git("status", "--porcelain").split("\n") if l.strip()]
    dirty = [l for l in dirty if not l.startswith("??")]
    if dirty:
        if lines:
            lines.append("")
        lines.append("커밋 안 된 변경 %d개 — 다른 세션 작업일 수 있습니다" % len(dirty))
        for l in dirty[:MAX_FILES]:
            lines.append("  " + l.strip())
        if len(dirty) > MAX_FILES:
            lines.append("  … 외 %d개" % (len(dirty) - MAX_FILES))
        lines.append("  git add -A 로 한꺼번에 담지 말 것. 내가 고친 파일만 담는다.")

    # 3. origin 과 어긋나 있으면 알린다.
    ab = git("rev-list", "--left-right", "--count", "HEAD...@{upstream}")
    if ab and "\t" in ab:
        ahead, behind = ab.split("\t")[:2]
        if behind != "0":
            if lines:
                lines.append("")
            lines.append("origin 이 %s개 앞서 있습니다 — 먼저 pull 하세요" % behind)
        elif ahead != "0":
            if lines:
                lines.append("")
            lines.append("안 올린 커밋 %s개" % ahead)

    # 4. 크론 간격 규칙을 어긴 슬롯이 있으면 알린다. 없으면 조용하다.
    #    세션이 셋이라 규칙을 기억에만 두면 다시 깨진다.
    try:
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "cron_check.py")],
                           cwd=ROOT, capture_output=True, timeout=20)
        if r.returncode == 1:
            if lines:
                lines.append("")
            lines.append(r.stdout.decode("utf-8", "replace").strip())
    except Exception:
        pass

    # 5. 이름을 바꿨는데 안 따라온 글이 있으면 알린다. 없으면 조용하다.
    #    숫자는 figures.py 가 발행 직전에 맞추지만 낱말은 못 잡는다.
    try:
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "stale_terms.py")],
                           cwd=ROOT, capture_output=True, timeout=20)
        if r.returncode == 1:
            if lines:
                lines.append("")
            lines.append(r.stdout.decode("utf-8", "replace").strip())
    except Exception:
        pass

    # 6. 작업일지의 마지막 날짜만 짚어 준다.
    note = ROOT / "docs" / "작업일지.md"
    if note.exists():
        for l in note.read_text(encoding="utf-8").split("\n"):
            # "## 적는 법" 같은 머리글이 아니라 날짜 줄만 잡는다.
            if l.startswith("## 20"):
                if lines:
                    lines.append("")
                lines.append("작업일지 최근: %s  (docs/작업일지.md)" % l[3:].strip())
                break

    if lines:
        print("\n".join(lines))


if __name__ == "__main__":
    main()
