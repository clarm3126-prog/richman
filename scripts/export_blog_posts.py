#!/usr/bin/env python3
"""탭 설명서를 네이버에 그대로 붙여 넣을 수 있는 글로 내보낸다.

make_blog_guides.py 는 마크다운을 만든다. 네이버 스마트에디터는 마크다운을
모르므로 `#`, `**`, `>` 가 글자 그대로 찍힌다. 그래서 한 번 더 바꾼다.

  # 제목      →  제목 + 밑줄
  ## 소제목   →  ▶ 소제목
  **굵게**    →  굵게 (기호만 뗀다)
  > 인용      →  인용 (기호만 뗀다)

**사진 자리를 글 안에 적어 둔다.** 탭마다 사진 한 장을 맨 위에 넣기로
했는데, 어느 파일을 어디에 넣는지는 글에 적혀 있지 않으면 매번 헷갈린다.
`[사진] …` 줄을 보고 그 자리에 올리면 된다. 올린 뒤에는 그 줄을 지운다.

이 스크립트가 없던 동안 docs/블로그글/*.txt 를 손으로 고쳤다. 그러면
생성기와 결과물이 조금씩 어긋난다. 실제로 2번 글에 영어 약자가 그대로
남은 적이 있다. 손으로 고치지 말고 여기서 다시 만든다.

사용:
  python scripts/export_blog_posts.py
  python scripts/export_blog_posts.py --tab industry
"""
import argparse
import io
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import make_blog_guides as G  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "docs" / "블로그글"
SHOT_DIR = "assets/screenshots/tabs"

# 글 순서. 이미 올린 글의 번호가 바뀌면 안 되므로 여기서 고정한다.
ORDER = ["minervini", "momentum", "strength", "industry", "movements",
         "themes", "movers", "newhighs", "investor", "volume",
         "oneil", "us", "watchlist", "journal"]

# 탭마다 넣을 사진. make_tab_shots 로 찍는다.
SHOTS = {k: f"t{i + 1:02d}-{k}.png" for i, k in enumerate(ORDER)}


def _table_row(line):
    """`| a | b |` 를 칸 목록으로. 표가 아니면 None."""
    s = line.strip()
    if not (s.startswith("|") and s.endswith("|")):
        return None
    return [c.strip() for c in s[1:-1].split("|")]


def to_plain(md):
    """마크다운을 네이버에 붙여 넣을 평문으로 바꾼다.

    표는 파이프 기호를 떼고 띄어쓰기로 칸을 벌린다. 에디터가 마크다운 표를
    모르므로 `|---|` 같은 줄이 글자 그대로 찍힌다.
    """
    out = []
    for line in md.split("\n"):
        cells = _table_row(line)
        if cells is not None:
            if all(set(c) <= set("-: ") for c in cells):
                continue                       # |---|---| 구분선은 버린다
            if cells and not cells[0]:         # 머리줄은 첫 칸이 비어 있다
                out += ["      " + "    ".join(cells[1:]), ""]
            else:
                out.append("   " + "    ".join(cells))
            continue
        if line.startswith("# "):
            out += [line[2:].strip(), "=" * 40]
            continue
        if line.startswith("## "):
            out.append("▶ " + line[3:].strip())
            continue
        if line.startswith("> "):
            line = line[2:]
        elif line.strip() == ">":
            line = ""
        out.append(re.sub(r"\*\*(.+?)\*\*", r"\1", line).rstrip())
    return "\n".join(out)


def insert_shot(text, shot):
    """머리말 다음, 첫 소제목 앞에 사진 자리를 끼운다.

    맨 위에 두면 목록에 뜨는 미리보기 그림이 되고, 글을 열자마자
    무슨 화면을 말하는지 보인다.
    """
    lines = text.split("\n")
    for i, l in enumerate(lines):
        if l.startswith("▶ "):
            cue = [f"[사진] {SHOT_DIR}/{shot}", ""]
            return "\n".join(lines[:i] + cue + lines[i:])
    return text + f"\n\n[사진] {SHOT_DIR}/{shot}\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tab")
    args = ap.parse_args()

    chips = G.chip_labels()
    keys = [args.tab] if args.tab else ORDER
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for key in keys:
        fn = G.BUILDERS.get(key)
        if not fn:
            print(f"모르는 탭: {key}")
            continue
        title, lines = fn(chips)
        text = insert_shot(to_plain("\n".join(lines)), SHOTS[key])

        # 파일 이름은 글 제목(첫 줄)에서 딴다. 빌더가 돌려주는 title 은
        # 목록에 쓰는 짧은 이름이라 글 제목과 다르다. 이미 올린 글의
        # 파일 이름이 바뀌면 어느 글을 올렸는지 헷갈린다.
        num = ORDER.index(key) + 1
        heading = text.split("\n", 1)[0].strip()
        path = OUT_DIR / f"{num:02d}_{heading.replace(' ', '_')}.txt"
        with io.open(path, "w", encoding="utf-8-sig", newline="\r\n") as f:
            f.write(text.rstrip() + "\n")

        shot = ROOT / SHOT_DIR / SHOTS[key]
        mark = "" if shot.exists() else "  ※ 사진 없음"
        print(f"{path.name}  {len(text):,}자{mark}")

    # 이 폴더에는 손으로 쓴 글도 같이 있다(16번부터). 그쪽에 마크다운이
    # 남아 있으면 네이버에서 별표가 글자로 찍힌다. 생성기가 내보낸 글은
    # 위에서 이미 걷어내므로, 남은 것은 손으로 쓴 글이다.
    left = []
    for f in sorted(OUT_DIR.glob("*.txt")):
        s = f.read_text(encoding="utf-8-sig")
        n = len(re.findall(r"\*\*", s)) + len(re.findall(r"^#{1,3} ", s, re.M))
        if n:
            left.append(f"{f.name} ({n}곳)")
    if left:
        print("\n※ 마크다운이 남은 글 — 네이버에서 기호가 그대로 찍힙니다")
        for x in left:
            print(f"   {x}")


if __name__ == "__main__":
    main()
