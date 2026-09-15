#!/usr/bin/env python3
"""예약 실행이 얼마나 늦게 뜨는지 잰다.

2026-09-14 에 예약이 최대 413분까지 밀려 그날 글이 어제 데이터로 나갔다.
원인을 갈라 보니 러너를 못 잡는 게 아니라 **GitHub 이 예약을 띄우는 순간
자체가 늦는 것**이었다. 그래서 크론을 정시에서 비껴 놓았다(2026-09-15).

효과가 있었는지는 며칠 지켜봐야 안다. 그때 분석을 다시 손으로 짜면 기준이
달라져 비교가 안 되므로 재는 방법을 여기 박아 둔다.

**두 가지를 따로 잰다.** 섞으면 원인을 못 가린다.

  예약 지연   크론 예정 시각 → 실행이 만들어진 시각 (GitHub 스케줄러)
  실행 대기   실행이 만들어진 시각 → 러너가 집어간 시각 (동시 실행 한도)

사용:
  python scripts/cron_lag.py                 최근 7일
  python scripts/cron_lag.py --days 14
  python scripts/cron_lag.py --compare       크론 옮기기 전후 비교
"""
import argparse
import datetime
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

ROOT = Path(__file__).resolve().parents[1]
REPO = "clarm3126-prog/richman"
# 크론을 정시에서 비껴 놓은 시각(UTC). 이 앞뒤로 갈라 본다.
#
# 날짜가 아니라 시각으로 자른다. 옮긴 날 오전은 아직 옛 크론으로 돌았기
# 때문에, 날짜로 자르면 그 반나절이 "옮긴 뒤"에 섞여 결과를 흐린다.
MOVED_AT = "2026-09-15T10:35"


def token():
    """git 자격증명에서 꺼낸다. 따로 넣어 둘 것이 없다."""
    import os
    for k in ("GH_TOKEN", "GITHUB_TOKEN"):
        if os.environ.get(k):
            return os.environ[k]
    out = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
                         capture_output=True, text=True, cwd=ROOT).stdout
    m = re.search(r"^password=(.+)$", out, re.M)
    return m.group(1) if m else None


def fetch_runs(tok, pages=3):
    runs = []
    for p in range(1, pages + 1):
        req = urllib.request.Request(
            f"https://api.github.com/repos/{REPO}/actions/runs?per_page=100&page={p}",
            headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"})
        runs += json.loads(urllib.request.urlopen(req, timeout=30).read())["workflow_runs"]
    return runs


def crons():
    """워크플로 이름 → 크론 목록."""
    out = {}
    for f in (ROOT / ".github/workflows").glob("*.yml"):
        t = f.read_text(encoding="utf-8")
        n = re.search(r"^name:\s*(.+)$", t, re.M)
        cs = re.findall(r'-\s*cron:\s*"([^"]+)"', t)
        if n and cs:
            out[n.group(1).strip()] = cs
    return out


def _field(spec, value, dow=False):
    if spec == "*":
        return True
    if spec.startswith("*/"):
        return value % int(spec[2:]) == 0
    got = set()
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-")
            got |= set(range(int(a), int(b) + 1))
        else:
            got.add(int(part))
    return value in got


def due(cron, now):
    """now 이전에 이 크론이 마지막으로 예정됐던 UTC 시각."""
    mi, h, _, _, dow = cron.split()
    for back in range(0, 60 * 24 * 3):
        t = (now - datetime.timedelta(minutes=back)).replace(second=0, microsecond=0)
        if (_field(mi, t.minute) and _field(h, t.hour)
                and _field(dow, (t.weekday() + 1) % 7)):
            return t
    return None


def measure(runs, cs, since=None, until=None):
    rows = []
    for r in runs:
        if r["event"] != "schedule" or r["name"] not in cs:
            continue
        at = datetime.datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")).replace(tzinfo=None)
        st = datetime.datetime.fromisoformat(r["run_started_at"].replace("Z", "+00:00")).replace(tzinfo=None)
        best = None
        for c in cs[r["name"]]:
            d = due(c, at)
            if d and (best is None or d > best):
                best, bc = d, c
        if not best:
            continue
        stamp = best.strftime("%Y-%m-%dT%H:%M")
        if since and stamp < since:
            continue
        if until and stamp >= until:
            continue
        # 몇 분마다 도는 크론은 늦어도 다음 슬롯에 붙어 지연이 0으로 보인다.
        # 자꾸 도는 것들이 평균을 눌러 버리므로 한 번짜리 크론만 센다.
        if bc.split()[0].startswith("*/"):
            continue
        rows.append({"name": r["name"], "cron": bc, "due": best,
                     "sched": (at - best).total_seconds() / 60,
                     "queue": (st - at).total_seconds() / 60})
    return rows


def stat(v):
    if not v:
        return "표본 없음"
    v = sorted(v)
    return (f"건수 {len(v):3}  중앙값 {v[len(v)//2]:5.0f}분  "
            f"평균 {sum(v)/len(v):5.0f}분  최대 {max(v):5.0f}분")


def report(rows, title):
    print(f"\n=== {title} ===")
    if not rows:
        print("  표본 없음")
        return
    print("  예약 지연 :", stat([r["sched"] for r in rows]))
    print("  실행 대기 :", stat([r["queue"] for r in rows]))
    worst = sorted(rows, key=lambda r: -r["sched"])[:5]
    print("  가장 늦었던 것")
    for r in worst:
        print(f"    {r['due'].strftime('%m-%d %H:%M')}Z  {r['sched']:5.0f}분  {r['name']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--compare", action="store_true", help="크론 옮기기 전후 비교")
    args = ap.parse_args()

    tok = token()
    if not tok:
        print("GitHub 토큰을 찾지 못했습니다 (git credential 또는 GH_TOKEN)")
        return 1
    runs = fetch_runs(tok)
    cs = crons()
    since = (datetime.date.today() - datetime.timedelta(days=args.days)).isoformat() + "T00:00"

    if args.compare:
        report(measure(runs, cs, until=MOVED_AT), f"크론 옮기기 전 (~{MOVED_AT} UTC)")
        report(measure(runs, cs, since=MOVED_AT), f"옮긴 뒤 ({MOVED_AT} UTC~)")
        print("\n  표본이 20건은 넘어야 견줄 만합니다. 평일 이틀이면 찹니다.")
    else:
        report(measure(runs, cs, since=since), f"최근 {args.days}일")
    return 0


if __name__ == "__main__":
    sys.exit(main())
