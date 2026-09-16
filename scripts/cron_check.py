#!/usr/bin/env python3
"""같은 워크플로의 슬롯이 두 시간 안에 붙어 있는지 검사한다.

GitHub 은 예약을 하루 몇 회로 나눠 주는 게 아니라 **최소 간격**을 강제한다.
실행 기록에서 연속 실행 사이 간격을 재면 바닥이 보인다.

    Social Engage  최소 113분  (하위 113·113·115·118·118·119…)
    User Alerts    최소 122분  (하위 122·136·137·138·138·141…)
    1슬롯짜리       1277~1451분 = 하루 한 번, 100% 뜬다

두 시간 안에 붙은 슬롯은 하나만 뜬다. 아무리 촘촘히 깔아도 두 시간에 한
번으로 솎인다. */15 가 하루 6~7회, */5(8시간 창)가 3~4회인 것이 전부 이
하나로 설명된다.

실제로 손해가 났다. social-post 의 18:40(자동)과 19:40(대기열)이 60분
간격이라 **저녁 두 글 중 하나가 매일 버려지고 있었다.** pipeline-watch 는
세 번 중 한 번만 떠서, 못 돈 것을 알리는 감시가 정작 한 번만 돌았다.

세션이 셋이라 규칙을 기억에만 두면 다시 깨진다. 그래서 센다. 세션을 열
때마다 자동으로 돌고, 위반이 없으면 아무것도 안 찍는다.

요일을 펼쳐서 본다. 안 펼치면 두 가지를 놓친다.
  · 평일 16:08 과 주말 16:08 을 0분이라 잘못 잡는다 (같은 날 안 뜬다)
  · 평일 슬롯에 요일 전용 슬롯을 더해 그 요일만 위반이 되는 것을 못 잡는다
"""
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
MIN_GAP = 120
DAYS = "일월화수목금토"          # cron 의 0=일요일


def expand(field, lo, hi):
    """cron 한 칸을 값 목록으로. */N · a-b · a,b · * 를 받는다."""
    if field == "*":
        return list(range(lo, hi + 1))
    if field.startswith("*/"):
        step = int(field[2:])
        return [v for v in range(lo, hi + 1) if v % step == 0]
    out = []
    for part in field.split(","):
        if "-" in part:
            a, b = part.split("-")
            out += list(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return sorted(set(out))


def slots(cron):
    """(KST 요일, KST 분) 목록. UTC→KST 에서 날이 넘어가는 것도 반영한다."""
    mi, hh, _, _, dow = cron.split()
    out = []
    for d in expand(dow, 0, 6):
        for h in expand(hh, 0, 23):
            for m in expand(mi, 0, 59):
                k = h + 9
                kd = (d + 1) % 7 if k >= 24 else d      # 자정을 넘으면 요일이 하루 밀린다
                out.append((kd, (k % 24) * 60 + m))
    return out


def main():
    bad = []
    for f in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        text = f.read_text(encoding="utf-8")
        crons = re.findall(r'-\s*cron:\s*"([^"]+)"', text)
        if len(crons) < 2:
            continue
        by_day = {}
        for c in crons:
            for d, m in slots(c):
                by_day.setdefault(d, []).append((m, c))
        for d, items in by_day.items():
            items.sort()
            for (m1, c1), (m2, c2) in zip(items, items[1:]):
                gap = m2 - m1
                if gap < MIN_GAP:
                    bad.append((f.name, DAYS[d], m1, m2, gap, c1, c2))

    if not bad:
        if "-v" in sys.argv:
            print("크론 간격 검사: 이상 없음")
        return 0

    print("크론 간격 검사 — 두 시간 안에 붙은 슬롯 %d건" % len(bad))
    print("  붙어 있으면 GitHub 이 하나만 띄웁니다. 어느 쪽이 뜰지는 모릅니다.")
    for name, day, m1, m2, gap, c1, c2 in bad:
        print("  %-22s %s  %02d:%02d → %02d:%02d  %d분" % (name, day, m1 // 60, m1 % 60,
                                                           m2 // 60, m2 % 60, gap))
        print("      %s   /   %s" % (c1, c2))
    return 1


if __name__ == "__main__":
    sys.exit(main())
