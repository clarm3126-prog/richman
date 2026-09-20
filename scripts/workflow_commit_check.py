#!/usr/bin/env python3
"""스크립트가 쓰는데 워크플로가 안 담는 파일을 찾는다.

2026-09-20 에 겪었다. `screener.py` 에 `data/screener_conditions_history/`
를 만들어 놓고 `screener.yml` 의 `git add` 목록에 안 넣었다. 깃허브에서
만들어지고 그대로 버려졌을 것이다.

**커밋이 안 되면 흔적도 안 남는다.** 에러도 없고 로그도 없다. 몇 달 뒤에
열어보고서야 "왜 아무것도 없지" 한다. 60일선 비교는 12월에나 열어볼
자료였으니 그때까지 몰랐을 것이다.

그래서 센다. 세션을 열 때마다 훑고, 깨끗하면 아무것도 안 찍는다.

**읽는 자리는 세지 않는다.** `Path("data/market.json")` 은 거의 다 읽는
자리다. 그걸 구멍이라고 하면 워크플로 아홉이 걸리고, 그런 경고는 결국
안 읽힌다. 실제로 쓰는 자리만 본다.

  python scripts/workflow_commit_check.py      깨끗하면 조용
  python scripts/workflow_commit_check.py -v   깨끗해도 한 줄 찍는다
"""
import ast
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows"

# 워크플로가 안 담아도 괜찮은 것들.
#   · 캐시나 중간 산물이라 남길 값어치가 없거나
#   · 다른 워크플로가 담당해서 여기서 담으면 서로 덮어쓴다
SKIP = {
    "data/trade_journal.json",   # 개인 매매 기록. .gitignore 에 있다
    "data/watchlist.json",       # 같음
    "data/alerts_config.json",   # 같음
}


def _path_literal(node):
    """Path("data/x") 또는 Path("data") / "x" 에서 경로 문자열을 뽑는다."""
    if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "Path":
        if node.args and isinstance(node.args[0], ast.Constant)                 and isinstance(node.args[0].value, str):
            v = node.args[0].value
            return v if v.startswith("data/") else None
        return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = _path_literal(node.left)
        if left and isinstance(node.right, ast.Constant) and isinstance(node.right.value, str):
            return left.rstrip("/") + "/" + node.right.value
        # Path("data") / f"{code}.json" — 디렉터리에 쓰는 꼴이다
        if left:
            return left
    return None


# 지우는 것(unlink)은 뺀다. 담을 파일을 만드는 게 아니다.
WRITE_ATTRS = {"write_text", "write_bytes", "mkdir", "touch"}


def _walk_scope(node):
    """이 마디 안을 훑되 **안쪽 함수로는 안 들어간다.**

    `ast.walk` 은 밑으로 다 파고든다. 그러면 모듈 차례에 모든 함수가
    한 통에 담겨, 함수마다 따로 보자던 것이 무너진다.
    """
    stack = list(ast.iter_child_nodes(node))
    while stack:
        n = stack.pop()
        yield n
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        stack.extend(ast.iter_child_nodes(n))


def _scope_writes(fn):
    """함수 하나 안에서 실제로 쓰는 data/ 경로.

    **함수 경계를 넘어 이름을 잇지 않는다.** `path` 는 파이썬에서 제일
    흔한 변수 이름이라, 파일 전체에서 이으면 읽기만 하는 함수의 경로가
    다른 함수의 쓰기에 딸려 걸린다. 실제로 `load_metadata()` 가
    그렇게 헛걸렸다.
    """
    nodes = list(_walk_scope(fn))
    names = {}
    out = set()

    # **이름을 먼저 다 모은다.** 한 번에 하면 순서에 걸린다 — 마디가
    # 늘 선언 순서로 나오지 않아서, 쓰기를 먼저 보면 이름이 아직
    # 안 이어져 있다. 그러면 잡아야 할 것을 놓치고 "이상 없음"을 찍는다.
    for n in nodes:
        if isinstance(n, ast.Assign) and len(n.targets) == 1                 and isinstance(n.targets[0], ast.Name):
            lit = _path_literal(n.value)
            if lit:
                names.setdefault(n.targets[0].id, set()).add(lit)

    for n in nodes:
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            if n.func.attr not in WRITE_ATTRS:
                continue
            # Path("data/x").write_text(...)
            lit = _path_literal(n.func.value)
            if lit:
                out.add(lit)
                continue
            # 변수로 쓰기 — 같은 함수 안에서 이름을 잇는다
            base = n.func.value
            if isinstance(base, ast.BinOp) and isinstance(base.op, ast.Div):
                base = base.left
            if isinstance(base, ast.Name):
                out |= names.get(base.id, set())
    return out


def written_paths(src):
    """이 소스가 실제로 **쓰는** data/ 경로."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return set()
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
            out |= _scope_writes(node)
    # 틀(f-string)은 경로가 아니다
    out = {p for p in out if "{" not in p}
    return {p for p in out if p not in SKIP}


def adds_of(text):
    """이 워크플로가 담는 경로. `<전부>` 면 git add -A 다."""
    if re.search(r"git add\s+(-A|\.)(\s|$)", text):
        return {"<전부>"}
    adds = set()
    for m in re.finditer(r"for f in ([^;]+);", text):
        adds |= {x.strip("\"'") for x in m.group(1).split() if "/" in x}
    for m in re.finditer(r"git add\s+([^\n|&]+)", text):
        if "$f" in m.group(1):
            continue
        adds |= {x.strip("\"'") for x in m.group(1).split() if "/" in x}
    return adds


def main():
    holes = []
    for y in sorted(WF.glob("*.yml")):
        text = y.read_text(encoding="utf-8")
        scripts = set(re.findall(r"python\s+(scripts/[\w/]+\.py)", text))
        if not scripts:
            continue
        adds = adds_of(text)
        if "<전부>" in adds:
            continue
        writes = set()
        for sp in sorted(scripts):
            p = ROOT / sp
            if p.exists():
                writes |= written_paths(p.read_text(encoding="utf-8"))
        for w in sorted(writes):
            if not any(w == a or w.startswith(a.rstrip("/") + "/") for a in adds):
                holes.append((y.name, w))

    if not holes:
        if "-v" in sys.argv:
            print("워크플로 커밋 목록: 이상 없음")
        return 0

    print("워크플로가 안 담는 파일 %d곳 — 만들어지고 그대로 버려집니다" % len(holes))
    for wf, path in holes:
        print("  %s  →  %s" % (wf, path))
    print("  (.github/workflows 의 git add 목록에 넣으세요)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
