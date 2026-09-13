#!/usr/bin/env python3
"""
SNS 프로필 사진을 그린다.

프로필 사진은 피드에서 40px 안팎으로 뜬다. 예전 아바타는 "stage2"를
가로로 얹어서 그 크기에서는 글자가 아예 뭉갰다. 그래서 두 가지를 지킨다.

1. 글자는 2x2로 접는다. "종목노트"를 한 줄로 쓰면 40px에서 못 읽는다.
2. 바탕을 강조색으로 꽉 채운다. 작은 크기에서 실제로 계정을 알아보게
   하는 건 글자가 아니라 색 덩어리다. 카드 위쪽 띠와 같은 주황이라
   피드에 뜬 카드와 프로필이 한 계정으로 읽힌다.

글자는 흰색이다. 수치상 대비는 진한 글씨가 더 높지만(4.98 대 3.56),
40px로 줄여보면 진한 획이 주황 중간톤에 묻혀 뭉개진다. 실제로 노출되는
크기에서 읽히는 쪽을 골랐다.

플랫폼이 정사각을 원으로 잘라내므로 글자를 안쪽 원 안에 둔다.
모서리는 잘려 나간다고 보면 된다.

사용:
  python scripts/make_avatar.py
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).parent))

from social import card  # noqa: E402

OUT = card.ROOT / "assets" / "avatar.png"

# 인스타는 320px로 줄여 쓰지만, 원본을 크게 올려두면 나중에 다른 데
# 쓸 때 다시 그릴 일이 없다.
S = 1024
LINES = ["종목", "노트"]
GAP = 30
FG = (255, 255, 255)


def render(out_path=OUT):
    img = Image.new("RGB", (S, S), card.ACCENT)
    d = ImageDraw.Draw(img)
    f = card._font(True, 300)

    # 폰트 메트릭에는 글자 위아래로 여백이 들어 있어서, 그 값으로 가운데를
    # 맞추면 눈에는 위로 치우쳐 보인다. 실제 글자 경계로 다시 잰다.
    boxes = [d.textbbox((0, 0), t, font=f) for t in LINES]
    heights = [b[3] - b[1] for b in boxes]

    y = (S - (sum(heights) + GAP * (len(LINES) - 1))) // 2
    for text, box, h in zip(LINES, boxes, heights):
        w = box[2] - box[0]
        d.text(((S - w) // 2 - box[0], y - box[1]), text, font=f, fill=FG)
        y += h + GAP

    return card._save(img, out_path)


def main():
    path = render()
    print(f"프로필 사진 생성: assets/{path.name}")


if __name__ == "__main__":
    main()
