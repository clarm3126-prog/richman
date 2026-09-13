#!/usr/bin/env python3
"""
SNS 프로필 사진을 그린다.

프로필 사진이 실제로 노출되는 크기는 피드에서 40px 안팎이다. 그 크기에서
읽히느냐가 전부고, 1024px로 봤을 때 예쁜지는 거의 상관이 없다.

예전에는 표식(꺾임선)만 크게 넣었다. 40px에서 가장 잘 읽히는 안이었지만
계정 이름과 이어지지 않았다. 이름이 '종목노트'인데 그림은 차트만 있어서
무엇을 하는 곳인지가 그림에 없었다.

그래서 노트를 바탕으로 깔고 그 위에 표식을 올린다. 흰 종이에 주황 선이다.
바탕과 표식의 색을 맞바꾼 셈이라 색 덩어리는 그대로 주황으로 읽힌다.

**제본 구멍은 두 개만 둔다.** 세 개로 그려 40px로 줄여 보니 서로 붙어
왼쪽에 얼룩 하나가 생겼다. 개수를 줄이고 간격을 넓혀야 구멍으로 읽힌다.

선은 주황이고 굵기를 64로 준다. 흰 바탕 위 주황은 주황 바탕 위 흰색보다
같은 굵기에서 가늘어 보여서, 예전 값(76)을 그대로 쓰면 흐려진다.

플랫폼이 정사각을 원으로 잘라내므로 노트를 안쪽에 넉넉히 넣는다.
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

# 인스타는 320px로 줄여 쓰지만, 원본을 크게 두면 나중에 다른 데 쓸 때
# 다시 그릴 일이 없다.
S = 1024
WHITE = (255, 255, 255)

# 노트 크기. 원으로 잘려도 남도록 안쪽에 둔다.
NOTE_W, NOTE_H = 580, 680
NOTE_RADIUS = 52

# 제본 구멍. 40px에서 붙지 않게 두 개만, 간격은 넓게.
HOLES = 2
HOLE_R = 34
HOLE_GAP = 150

# 선 두께. 흰 바탕 위 주황이라 예전(76)보다 얇게 잡아도 또렷하다.
STROKE = 64

# 표식의 뼈대. (0,0)~(1,1) 정규 좌표이고 y는 아래가 크다.
# 앞쪽 절반은 바닥 다지기라 위아래로 조금씩 흔들리고, 뒤쪽에서 위로 뚫는다.
SKELETON = [
    (0.00, 0.86),
    (0.14, 0.79),
    (0.26, 0.92),
    (0.40, 0.74),
    (0.52, 0.83),
    (0.72, 0.44),
    (1.00, 0.00),
]


def render(out_path=OUT):
    img = Image.new("RGB", (S, S), card.ACCENT)
    d = ImageDraw.Draw(img)

    nx, ny = (S - NOTE_W) / 2, (S - NOTE_H) / 2
    d.rounded_rectangle([nx, ny, nx + NOTE_W, ny + NOTE_H],
                        radius=NOTE_RADIUS, fill=WHITE)

    top = ny + (NOTE_H - HOLE_GAP * (HOLES - 1)) / 2 - 150
    for i in range(HOLES):
        cy = top + i * HOLE_GAP
        d.ellipse((nx + 60, cy - HOLE_R, nx + 60 + HOLE_R * 2, cy + HOLE_R),
                  fill=card.ACCENT)

    x0, y0 = nx + 168, ny + 300
    w, h = NOTE_W - 250, 250
    pts = [(x0 + px * w, y0 + py * h) for px, py in SKELETON]
    d.line(pts, fill=card.ACCENT, width=STROKE, joint="curve")

    # joint="curve"는 이음매만 둥글게 하고 양 끝은 각진 채로 둔다.
    # 끝에 원을 하나씩 찍어야 선이 잘린 것처럼 보이지 않는다.
    r = STROKE / 2
    for x, y in (pts[0], pts[-1]):
        d.ellipse((x - r, y - r, x + r, y + r), fill=card.ACCENT)

    return card._save(img, out_path)


def main():
    path = render()
    print(f"프로필 사진 생성: assets/{path.name}")


if __name__ == "__main__":
    main()
