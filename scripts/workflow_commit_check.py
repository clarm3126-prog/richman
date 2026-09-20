#!/usr/bin/env python3
"""스크립트가 쓰는데 워크플로가 안 담는 파일을 찾는다.

2026-09-20 에 겪었다. `screener.py` 에 `data/screener_conditions_history/`
를 만들어 놓고 `screener.yml` 의 `git add` 목록에 안 넣었다. 깃허브에서
만들어지고 그대로 버려졌을 것이다.

**커밋이 안 되면 흔적도 안 남는다.** 에러도 없고 로그도 없다. 몇 달 뒤에
열어보고서야 "왜 아무것도 없지" 한다. 60일선 비교는 12월에나 열어볼
자료였으니 그때까지 몰랐을 것이다.

**읽는 자리는 세지 않는다.** `Path("data/market.json")` 은 거의 다 읽는
자리다. 그걸 구멍이라고 하면 워크플로 아홉이 걸리고, 그런 경고는 결국
안 읽힌다. 실제로 쓰는 자리만 본다.

■ 짜면서 네 번 틀렸다. 남겨 둔다.

  1. 정규식으로 이름을 이었더니 **함수 경계를 넘었다.** `load_metadata()` 의
     읽기 경로가 다른 함수의 `path.write_text` 에 딸려 걸렸다. `path` 는
     파이썬에서 제일 흔한 변수 이름이다.
  2. 경계를 막았더니 이번엔 **마디 순서** 탓에 쓰기를 먼저 보고 이름을
     나중에 봤다. 잡아야 할 두 곳을 놓치면서 "이상 없음"을 찍었다.
  3. `Path("data/...")` 로 **시작하는 것만** 따라가서 옆 세션 코드를 통째로
     못 봤다. `ROOT / "data" / "social"` 도 `save_json(파일, ...)` 도 안
     보였다. 그쪽 워크플로가 안전한 건 사실이었지만 **이 자가 침묵한 것은
     근거가 아니었다.**
  4. 그걸 고치며 **불러온 모듈을 통째로 따라갔다.** `earnings.py` 가
     `screener` 를 불러 쓴다고 해서 스크리너의 모든 저장을 하는 게 아닌데,
     워크플로 여섯이 **56곳**으로 걸렸다. 그런 경고는 아무도 안 읽는다.
     지금은 **실제로 부르는 함수의 쓰기만** 따라간다.

앞의 셋은 **못 잡으면서 조용한** 고장이고, 넷째는 **안 틀린 것을 잡는**
고장이다. 둘 다 결과는 같다 — 아무도 안 보게 된다. 그래서 스스로 시험하는
자리를 뒀다. 규칙을 고쳤으면 `--test` 를 돌려라.

  python scripts/workflow_commit_check.py        깨끗하면 조용
  python scripts/workflow_commit_check.py -v     깨끗해도 한 줄 찍는다
  python scripts/workflow_commit_check.py --test 잡을 힘이 있는지 시험한다
"""
import ast
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows"

# 워크플로가 안 담아도 되는 것들. .gitignore 에 있는 개인 자료다.
SKIP = {
    "data/trade_journal.json",
    "data/watchlist.json",
    "data/alerts_config.json",
}

# 경로에 쓰는 Path 메서드
WRITE_ATTRS = {"write_text", "write_bytes", "mkdir", "touch"}
# 경로를 첫 인자로 받아 쓰는 도우미. 이 저장소에서 쓰는 이름만 적는다.
WRITE_FUNCS = {"save_json", "write_json", "dump_json", "save_text", "atomic_write"}


def _literal(node, names):
    """이 마디가 가리키는 data/ 경로. 모르면 None.

    `"data/x"` · `Path("data/x")` · `Path("data") / "x"` ·
    `ROOT / "data" / "social"` · `STATE_DIR / f"{name}.json"` 을 따라간다.
    뒤의 둘을 놓쳐서 옆 세션 코드를 통째로 못 본 적이 있다.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        v = node.value
        return v if v == "data" or v.startswith("data/") else None
    if isinstance(node, ast.Name):
        got = names.get(node.id)
        return next(iter(got)) if got and len(got) == 1 else None
    if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "Path":
        return _literal(node.args[0], names) if node.args else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = _literal(node.left, names)
        if left is None:
            # ROOT / "data" / ... — 왼쪽을 몰라도 "data" 면 거기서 시작한다
            if isinstance(node.right, ast.Constant) and node.right.value == "data":
                return "data"
            return None
        if isinstance(node.right, ast.Constant) and isinstance(node.right.value, str):
            return left.rstrip("/") + "/" + node.right.value
        # 변수나 f-string 이 붙으면 그 위 디렉터리까지만 안다
        return left
    return None


def _assigns(nodes, names):
    """`이름 = 경로` 를 모은다. 한 이름이 여러 경로를 가리킬 수 있다."""
    for n in nodes:
        if isinstance(n, ast.Assign) and len(n.targets) == 1 \
                and isinstance(n.targets[0], ast.Name):
            lit = _literal(n.value, names)
            if lit:
                names.setdefault(n.targets[0].id, set()).add(lit)


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


def _writes_in(nodes, names):
    """이 마디들 안에서 실제로 쓰는 data/ 경로."""
    out = set()
    for n in nodes:
        if not isinstance(n, ast.Call):
            continue
        # path.write_text(...) · dir.mkdir(...)
        if isinstance(n.func, ast.Attribute) and n.func.attr in WRITE_ATTRS:
            lit = _literal(n.func.value, names)
            if lit:
                out.add(lit)
        # save_json(path, ...) 같은 도우미
        fname = getattr(n.func, "id", None) or getattr(n.func, "attr", None)
        if fname in WRITE_FUNCS and n.args:
            lit = _literal(n.args[0], names)
            if lit:
                out.add(lit)
        # open("data/x", "w") — json.dump 와 함께 쓰는 꼴
        if getattr(n.func, "id", "") == "open" and len(n.args) >= 2:
            mode = n.args[1]
            if isinstance(mode, ast.Constant) and isinstance(mode.value, str) \
                    and ("w" in mode.value or "a" in mode.value):
                lit = _literal(n.args[0], names)
                if lit:
                    out.add(lit)
    return out


def _tidy(paths):
    """틀은 빼고, 위 디렉터리가 이미 있으면 그 안의 파일은 따로 말하지 않는다."""
    out = {p for p in paths if "{" not in p and p != "data"}
    out = {p for p in out
           if not any(o != p and p.startswith(o.rstrip("/") + "/") for o in out)}
    return {p for p in out if p not in SKIP}


def _scopes(tree):
    """(함수 이름 또는 None, 그 안의 마디들, 그 안에서 보이는 이름들)."""
    top = list(_walk_scope(tree))
    globals_ = {}
    _assigns(top, globals_)
    yield None, top, globals_
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            body = list(_walk_scope(node))
            # 모듈 바깥에 둔 상수는 함수 안에서도 보인다 (STATE_DIR 같은 것).
            # 함수 안의 이름은 그 함수 안에서만 본다 — `path` 가 흔해서다.
            names = {k: set(v) for k, v in globals_.items()}
            # **이름을 먼저 다 모은다.** 쓰기를 먼저 보면 이름이 아직
            # 안 이어져 있어, 잡아야 할 것을 놓치고 "이상 없음"을 찍는다.
            _assigns(body, names)
            yield node.name, body, names


def written_paths(src):
    """이 소스가 실제로 **쓰는** data/ 경로."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return set()
    out = set()
    for _, body, names in _scopes(tree):
        out |= _writes_in(body, names)
    return _tidy(out)


def func_writes(src):
    """함수 이름 → 그 함수가 쓰는 경로."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return {}
    out = {}
    for name, body, names in _scopes(tree):
        if name is None:
            continue
        w = _tidy(_writes_in(body, names))
        if w:
            out[name] = w
    return out


def borrowed_names(src, module):
    """`module` 에서 가져다 **실제로 부르는** 이름들.

    이름만 맞춰 이으면 안 된다. `main` 은 어느 스크립트에나 있어서,
    스크리너의 `main` 이 쓰는 파일이 backtest 쪽으로 딸려 왔다. 흔한
    이름 하나가 워크플로 다섯을 헛걸리게 했다.

    그래서 **어디서 가져왔는지까지 본다.**
      from screener import save_chart_data  →  save_chart_data
      import screener; screener.save_chart_data()  →  save_chart_data
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return set()
    leaf = module.split("/")[-1]           # scripts/social/store → store
    alias = set()                          # 이 모듈을 가리키는 이름
    named = set()                          # from ... import 로 가져온 이름
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a_ in n.names:
                if a_.name.split(".")[-1] == leaf:
                    alias.add(a_.asname or a_.name.split(".")[-1])
        elif isinstance(n, ast.ImportFrom):
            if (n.module or "").split(".")[-1] == leaf:
                named |= {a_.asname or a_.name for a_ in n.names}
    out = set()
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        if isinstance(f, ast.Name) and f.id in named:
            out.add(f.id)
        elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)                 and f.value.id in alias:
            out.add(f.attr)
    return out


def adds_of(text):
    """이 워크플로가 담는 경로. `<전부>` 면 git add -A 다."""
    if re.search(r"git add\s+(-A|\.)(\s|$)", text):
        return {"<전부>"}
    # `if [ -e x ]; then git add x; fi` 처럼 한 줄에 붙여 쓴 것도 읽는다.
    # 세미콜론이 붙은 채로 잡으면 목록과 안 맞아, **담고 있는데 안 담는다고**
    # 말하게 된다. 헛걸림 하나가 남으면 다음 사람은 목록 전체를 안 믿는다.
    def clean(tok):
        return tok.strip("\"'`;()").rstrip(";")

    adds = set()
    for m in re.finditer(r"for f in ([^;]+);", text):
        adds |= {clean(x) for x in m.group(1).split() if "/" in x}
    for m in re.finditer(r"git add\s+([^\n|&]+)", text):
        if "$f" in m.group(1):
            continue
        adds |= {clean(x) for x in m.group(1).split() if "/" in x}
    return {a for a in adds if a}


def writes_for(workflow_text):
    """이 워크플로가 실제로 남기는 경로와, 살펴본 스크립트들.

    **불러온 모듈을 통째로 따라가면 안 된다.** `earnings.py` 가 `screener`
    를 불러 쓴다고 해서 스크리너의 모든 저장을 하는 게 아니다. 그렇게
    셌더니 워크플로 여섯이 56곳으로 걸렸다.

    대신 **부르는 함수의 쓰기만** 따라간다. `momentum_screener.py` 가
    `screener.save_chart_data` 를 부르면 `data/charts` 가 잡힌다.
    """
    entries = re.findall(r"python\s+(scripts/[\w/]+\.py)", workflow_text)
    if not entries:
        return set(), set()
    out, seen = set(), set()
    for sp in entries:
        p = ROOT / sp
        if not p.exists():
            continue
        seen.add(sp)
        src = p.read_text(encoding="utf-8")
        out |= written_paths(src)
        for m in re.finditer(r"^\s*(?:import|from)\s+([\w.]+)", src, re.M):
            mod = m.group(1).replace(".", "/")
            for cand in ("scripts/%s.py" % mod, "scripts/%s/__init__.py" % mod):
                mp = ROOT / cand
                if not mp.exists() or cand in seen:
                    continue
                seen.add(cand)
                borrowed = borrowed_names(src, mod)
                if not borrowed:
                    continue
                for fname, paths in func_writes(mp.read_text(encoding="utf-8")).items():
                    if fname in borrowed:
                        out |= paths
    return _tidy(out), seen


def holes():
    found = []
    for y in sorted(WF.glob("*.yml")):
        text = y.read_text(encoding="utf-8")
        writes, scripts = writes_for(text)
        if not scripts:
            continue
        adds = adds_of(text)
        if "<전부>" in adds:
            continue
        for w in sorted(writes):
            if not any(w == a or w.startswith(a.rstrip("/") + "/") for a in adds):
                found.append((y.name, w))
    return found


CASES = [
    ('Path("data/a.json").write_text(x)', {"data/a.json"}),
    ('def f():\n    p = Path("data/b.json")\n    p.write_text(x)', {"data/b.json"}),
    ('def f():\n    p = Path("data/c.json")\n    return load_json(p, {})', set()),
    ('ROOT = Path(__file__).parents[1]\nD = ROOT / "data" / "social"\n'
     'def f():\n    D.mkdir(parents=True, exist_ok=True)', {"data/social"}),
    ('ROOT = Path(__file__).parents[1]\nD = ROOT / "data" / "social"\n'
     'def f(n):\n    (D / f"{n}.json").write_text(x)', {"data/social"}),
    ('F = Path("data/off.json")\ndef f():\n    save_json(F, {})', {"data/off.json"}),
    ('def f():\n    json.dump(o, open("data/e.json", "w"))', {"data/e.json"}),
    ('def f():\n    d = Path("data/hist")\n    d.mkdir()\n'
     '    (d / "x.json").write_text(y)', {"data/hist"}),
    # 같은 이름을 두 함수가 쓰는 꼴 — 둘 다 잡아야 한다
    ('def a():\n    path = Path("data/p.json")\n    path.write_text(x)\n'
     'def b():\n    path = Path("data/q.json")\n    path.write_text(x)',
     {"data/p.json", "data/q.json"}),
    # 읽기만 하는 함수의 경로가 딸려 오면 안 된다
    ('def a():\n    path = Path("data/r.json")\n    return load_json(path, {})\n'
     'def b():\n    path = Path("data/s.json")\n    path.write_text(x)', {"data/s.json"}),
]


def selftest():
    """잡을 힘이 있는지, 그리고 헛걸리지 않는지 시험한다.

    "이상 없음"은 잡을 힘이 있을 때만 뜻이 있다. 이 자는 두 번, 못 잡으면서
    이상 없음을 찍었다. 한 번은 반대로 56곳을 헛걸었다.
    """
    bad = 0
    for src, want in CASES:
        got = written_paths(src)
        ok = got == want
        bad += not ok
        head = src.split("\n")[0][:44]
        print("  %s %-46s 얻음 %s" % ("✅" if ok else "❌", head, sorted(got) or "없음"))
        if not ok:
            print("     바랐던 것: %s" % (sorted(want) or "없음"))
    print("\n시험 %d개 중 %d개 실패" % (len(CASES), bad))
    return 1 if bad else 0


def main():
    if "--test" in sys.argv:
        return selftest()
    found = holes()
    if not found:
        if "-v" in sys.argv:
            print("워크플로 커밋 목록: 이상 없음")
        return 0
    print("워크플로가 안 담는 파일 %d곳 — 만들어지고 그대로 버려집니다" % len(found))
    for wf, path in found:
        print("  %s  →  %s" % (wf, path))
    print("  (.github/workflows 의 git add 목록에 넣으세요)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
