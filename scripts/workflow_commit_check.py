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

  5. **담는 쪽을 못 읽어 양쪽이 다 비었다.** 루프 변수를 `f` 로만 알아봐서
     `for p in data/social; do ... git add "$p"` 를 통째로 못 읽었고, 같은
     워크플로의 쓰기도 `from social import store` 꼴을 안 이어 못 봤다.
     **쓴다 0 · 담는다 0 이면 구멍이 나올 수가 없다. 조용한 게 통과가
     아니었다.** 둘 중 하나만 고쳤으면 그 워크플로가 통째로 헛걸렸다.

■ 이 자가 못 보는 것 — 알고 쓰라

  · **경로를 인자로 받아 쓰는 함수.** `card.py` 의 `_save(img, out_path)`
    처럼 부르는 쪽이 경로를 정하면 여기서는 알 수 없다. 카드 이미지가
    그렇다(`social-post.yml` 이 `assets/cards` 를 담고 있어 지금은 구멍이
    아니다). 그런 자리에 새 경로가 생기면 **말이 없으니 사람이 봐야 한다.**
  · `data/` 와 `assets/` 밖. 다른 뿌리를 쓰기 시작하면 `ROOTS` 에 더해라.

  python scripts/workflow_commit_check.py        깨끗하면 조용
  python scripts/workflow_commit_check.py -v     깨끗해도 한 줄 찍는다
  python scripts/workflow_commit_check.py --test 잡을 힘이 있는지 시험한다
  python scripts/workflow_commit_check.py --map  워크플로마다 쓴다/담는다를 찍는다
  python scripts/workflow_commit_check.py --prove 담는 줄을 지워 보고 짚는지 센다
"""
import ast
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows"

# 저장소에 남겨야 하는 뿌리들. 카드 이미지가 assets/ 밑에 쌓인다.
ROOTS = ("data", "assets")

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
        return v if any(v == r or v.startswith(r + "/") for r in ROOTS) else None
    if isinstance(node, ast.Name):
        got = names.get(node.id)
        return next(iter(got)) if got and len(got) == 1 else None
    if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "Path":
        return _literal(node.args[0], names) if node.args else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = _literal(node.left, names)
        if left is None:
            # ROOT / "data" / ... — 왼쪽을 몰라도 뿌리 이름이면 거기서 시작한다
            if isinstance(node.right, ast.Constant) and node.right.value in ROOTS:
                return node.right.value
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
    out = {p for p in paths if "{" not in p and p not in ROOTS}
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


def _module_file(mod):
    """`social/store` → `scripts/social/store.py`. 없으면 None."""
    for cand in ("scripts/%s.py" % mod, "scripts/%s/__init__.py" % mod):
        if (ROOT / cand).exists():
            return cand
    return None


def module_uses(src):
    """이 소스가 **가져다 부르는** 것들. `{모듈경로: {이름, ...}}`

    이름만 맞춰 이으면 안 된다. `main` 은 어느 스크립트에나 있어서,
    스크리너의 `main` 이 쓰는 파일이 backtest 쪽으로 딸려 왔다. 흔한
    이름 하나가 워크플로 다섯을 헛걸리게 했다. 그래서 **어디서
    가져왔는지까지** 본다. 세 꼴을 다 잇는다.

      import screener                      →  screener.save_chart_data()
      from screener import save_chart_data →  save_chart_data()
      from social import store             →  store.save()   ← 모듈을 이름으로

    마지막 꼴을 빠뜨려 `social/store.py` 의 `data/social` 쓰기를 통째로
    못 봤다. 그러면서 담는 쪽도 못 읽어 **양쪽이 다 비어 조용했다.
    통과가 아니라 안 본 것이었다.**
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return {}
    alias = {}      # 이름 → 모듈경로 (모듈을 통째로 가리키는 이름)
    named = {}      # 이름 → 모듈경로 (from ... import 로 가져온 함수)
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a_ in n.names:
                mod = a_.name.replace(".", "/")
                if _module_file(mod):
                    alias[a_.asname or a_.name.split(".")[-1]] = mod
        elif isinstance(n, ast.ImportFrom):
            base = (n.module or "").replace(".", "/")
            for a_ in n.names:
                sub = (base + "/" + a_.name) if base else a_.name
                if _module_file(sub):
                    # from social import store — 모듈을 이름으로 가져온 꼴
                    alias[a_.asname or a_.name] = sub
                elif _module_file(base):
                    named[a_.asname or a_.name] = base

    out = {}
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        if isinstance(f, ast.Name) and f.id in named:
            out.setdefault(named[f.id], set()).add(f.id)
        elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) \
                and f.value.id in alias:
            out.setdefault(alias[f.value.id], set()).add(f.attr)
    return out


def _clean_token(tok):
    """셸 토큰에서 경로만 남긴다. 경로가 아니면 빈 문자열."""
    t = tok.strip("\"'`;()").rstrip(";")
    if not t or "/" not in t:
        return ""
    # `2>/dev/null` 같은 것이 `/` 만 보고 섞여 들어온다
    if ">" in t or "<" in t or t.startswith("-") or t.startswith("/dev/"):
        return ""
    return t


def adds_of(text):
    """이 워크플로가 담는 경로. `<전부>` 면 git add -A 다.

    ⚠️ **담는 쪽을 못 읽으면 쓰는 쪽이 통째로 헛걸린다.** 실제로 루프
    변수를 `f` 로만 알아봐서 `for p in data/social; do ... git add "$p"` 를
    통째로 못 읽었다. 그때 쓰는 쪽까지 못 보고 있어 양쪽이 다 비었고,
    그래서 **조용했다. 통과가 아니라 안 본 것이었다.**
    한쪽만 고치면 그 워크플로의 모든 경로가 헛걸린다.
    """
    if re.search(r"git add\s+(-A|\.)(\s|$)", text):
        return {"<전부>"}

    # `for <아무 이름> in a b c; do` — 루프 변수 이름은 f 일 수도 p 일 수도 있다
    loops = {}
    for m in re.finditer(r"for\s+(\w+)\s+in\s+([^;\n]+)[;\n]", text):
        paths = {_clean_token(x) for x in m.group(2).split()}
        paths.discard("")
        if paths:
            loops[m.group(1)] = paths

    adds = set()
    for m in re.finditer(r"git add\s+([^\n|&]+)", text):
        arg = m.group(1)
        hit = False
        for var, paths in loops.items():
            if "$" + var in arg or "${" + var + "}" in arg:
                adds |= paths
                hit = True
        if hit:
            continue          # 루프 변수를 담는 줄이면 그 목록이 답이다
        toks = {_clean_token(x) for x in arg.split()}
        toks.discard("")
        adds |= toks
    return adds


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
        for mod, borrowed in module_uses(src).items():
            cand = _module_file(mod)
            if not cand:
                continue
            seen.add(cand)
            for fname, paths in func_writes((ROOT / cand).read_text(encoding="utf-8")).items():
                if fname in borrowed:
                    out |= paths
    return _tidy(out), seen


def holes():
    """(구멍, 눈먼 곳).

    **구멍**은 쓰는데 안 담는 것이다.

    **눈먼 곳**은 담는데 쓰는 걸 못 본 것이다. 무언가를 커밋하고 있다면
    누군가는 그걸 쓴다. 그런데 이 자에게 쓰기가 안 보이면, 그 워크플로에
    대해서는 **아무 말도 할 수 없는 상태**다. 그걸 "이상 없음"으로 읽으면
    안 된다 — 실제로 `social-engage` · `social-post` 가 양쪽 다 비어
    조용했고, 통과가 아니라 안 본 것이었다(2026-09-20, 12318 이 잡음).

    쓰지도 담지도 않는 워크플로(점검·알림 전용)는 둘 다 비는 게 맞다.
    """
    found, blind = [], []
    for y in sorted(WF.glob("*.yml")):
        text = y.read_text(encoding="utf-8")
        writes, scripts = writes_for(text)
        if not scripts:
            continue
        adds = adds_of(text)
        if "<전부>" in adds:
            continue
        if adds and not writes:
            blind.append((y.name, sorted(adds)))
            continue
        for w in sorted(writes):
            if not any(w == a or w.startswith(a.rstrip("/") + "/") for a in adds):
                found.append((y.name, w))
    return found, blind


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
    # data/ 만 보다가 카드 이미지를 놓쳤다. assets/ 도 본다.
    ('def f():\n    p = Path("assets/cards") / "a.jpg"\n    p.write_bytes(b)',
     {"assets/cards/a.jpg"}),
    # 읽기만 하는 함수의 경로가 딸려 오면 안 된다
    ('def a():\n    path = Path("data/r.json")\n    return load_json(path, {})\n'
     'def b():\n    path = Path("data/s.json")\n    path.write_text(x)', {"data/s.json"}),
]


# 담는 쪽 시험. **쓰는 쪽만 시험하면 반쪽이다.**
# 실제로 루프 변수를 `f` 로만 알아봐서 `for p in ...` 을 통째로 못 읽었다.
# 그때 쓰는 쪽도 못 보고 있어 양쪽이 비었고, 그래서 조용했다.
ADD_CASES = [
    ("for f in data/a.json data/b; do\n  git add \"$f\"\ndone", {"data/a.json", "data/b"}),
    # 루프 변수 이름은 f 가 아닐 수 있다
    ("for p in data/social; do\n  if [ -e \"$p\" ]; then git add \"$p\"; fi\ndone",
     {"data/social"}),
    ("if [ -e data/w.json ]; then git add data/w.json; fi", {"data/w.json"}),
    ("git add data/x.json assets/cards", {"data/x.json", "assets/cards"}),
    ("git add -A", {"<전부>"}),
    # `2>/dev/null` 이 `/` 만 보고 섞여 들어오던 것
    ("git add data/y.json 2>/dev/null || true", {"data/y.json"}),
]


def selftest():
    """잡을 힘이 있는지, 그리고 헛걸리지 않는지 시험한다.

    "이상 없음"은 잡을 힘이 있을 때만 뜻이 있다. 이 자는 두 번, 못 잡으면서
    이상 없음을 찍었다. 한 번은 반대로 56곳을 헛걸었다.

    **쓰는 쪽과 담는 쪽을 둘 다 시험한다.** 한쪽만 보면 양쪽이 비어서
    조용한 것을 통과로 읽는다.
    """
    bad = 0
    print("■ 쓰는 쪽")
    for src, want in CASES:
        got = written_paths(src)
        ok = got == want
        bad += not ok
        head = src.split("\n")[0][:44]
        print("  %s %-46s 얻음 %s" % ("✅" if ok else "❌", head, sorted(got) or "없음"))
        if not ok:
            print("     바랐던 것: %s" % (sorted(want) or "없음"))
    print("\n■ 담는 쪽")
    for text, want in ADD_CASES:
        got = adds_of(text)
        ok = got == want
        bad += not ok
        head = text.split("\n")[0][:44]
        print("  %s %-46s 얻음 %s" % ("✅" if ok else "❌", head, sorted(got) or "없음"))
        if not ok:
            print("     바랐던 것: %s" % (sorted(want) or "없음"))
    total = len(CASES) + len(ADD_CASES)
    print("\n시험 %d개 중 %d개 실패" % (total, bad))
    return 1 if bad else 0


def prove():
    """워크플로마다 **담는 줄을 지워 보고** 이 자가 짚는지 센다.

    훑어서 0곳이 나오는 건 여전히 "못 잡아서"일 수 있다. 일부러
    망가뜨려야 안다. 12318 이 쓴 방법을 자에 넣었다.

    파일은 안 건드린다. 본문만 메모리에서 바꿔 넣는다.
    """
    bad = 0
    shown = 0
    for y in sorted(WF.glob("*.yml")):
        text = y.read_text(encoding="utf-8")
        writes, scripts = writes_for(text)
        if not scripts:
            continue
        adds = adds_of(text)
        if not adds or "<전부>" in adds:
            continue
        shown += 1
        broken = re.sub(r".*git add.*", "", text)
        b_adds = adds_of(broken)
        missed = [w for w in sorted(writes)
                  if not any(w == a or w.startswith(a.rstrip("/") + "/") for a in b_adds)]
        bad += not missed
        print("  %s %-28s %s" % (
            "✅" if missed else "❌", y.name,
            ", ".join(missed[:3]) if missed else "아무것도 안 잡음 — 눈먼 곳"))
    print("\n담는 줄이 있는 워크플로 %d개 가운데 %s"
          % (shown, "전부 잡습니다" if not bad else "%d개를 못 잡습니다" % bad))
    return 1 if bad else 0


def show_map():
    """워크플로마다 `쓴다 / 담는다` 를 나란히 찍는다.

    **검사기가 조용한 까닭이 "없다"인지 "안 봤다"인지를 가른다.**
    양쪽이 다 비면 통과가 아니다 — 안 본 것일 수 있다. 실제로 제
    워크플로 둘이 그렇게 조용했다(2026-09-20, 12318 이 잡음).
    쓰지도 담지도 않는 워크플로(점검·알림만 하는 것)면 비는 게 맞다.
    """
    for y in sorted(WF.glob("*.yml")):
        text = y.read_text(encoding="utf-8")
        writes, scripts = writes_for(text)
        if not scripts:
            continue
        adds = adds_of(text)
        mark = "  ← 양쪽이 빔. 정말 안 남기는지 보라" if not writes and not adds else ""
        print("%-28s 쓴다 %-2d  담는다 %-2d%s" % (y.name, len(writes), len(adds), mark))
    return 0


def main():
    if "--test" in sys.argv:
        return selftest()
    if "--map" in sys.argv:
        return show_map()
    if "--prove" in sys.argv:
        return prove()
    found, blind = holes()
    if not found and not blind:
        if "-v" in sys.argv:
            print("워크플로 커밋 목록: 이상 없음")
        return 0
    if found:
        print("워크플로가 안 담는 파일 %d곳 — 만들어지고 그대로 버려집니다" % len(found))
        for wf, path in found:
            print("  %s  →  %s" % (wf, path))
        print("  (.github/workflows 의 git add 목록에 넣으세요)")
    if blind:
        if found:
            print("")
        # 담는데 쓰는 걸 못 봤다. 이 워크플로에 대해서는 아무 말도 할 수
        # 없는 상태다. "이상 없음"으로 읽으면 안 된다.
        print("담고는 있는데 쓰는 자리를 못 찾은 워크플로 %d개 — 검사기가 눈먼 곳입니다" % len(blind))
        for wf, adds in blind:
            print("  %s  담는다: %s" % (wf, ", ".join(adds)))
        print("  (경로를 인자로 받아 쓰거나, 이 자가 모르는 꼴일 수 있습니다.")
        print("   손으로 확인하고 --test 에 그 꼴을 사례로 넣으세요)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
