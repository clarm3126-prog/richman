#!/usr/bin/env python3
"""
SNS 프로필 사진을 그린다.

프로필 사진이 실제로 노출되는 크기는 피드에서 40px 안팎이다. 그 크기에서
읽히느냐가 전부고, 1024px로 봤을 때 예쁜지는 거의 상관이 없다.

예전 안은 "종목노트"를 2x2로 접어 넣었다. 한 줄보다는 나았지만 40px로
줄여보면 네 글자의 획이 서로 붙어 흰 얼룩이 된다. 실제로 줄여서 확인했다.
한글 네 글자는 그 크기에 담을 수 있는 정보량을 넘는다.

그래서 글자를 빼고 표식 하나만 남긴다.

  바닥을 다지다 위로 뚫고 나가는 선

stage2.kr이라는 이름이 가리키는 것이 그대로 이 모양이다. 주가가 오래
눌려 있다가(1단계) 방향을 틀어 올라가는(2단계) 구간만 찾는다는 뜻이고,
사이트가 하는 일과 표식이 같은 말을 한다.

바탕은 카드 위쪽 띠와 같은 주황(card.ACCENT)이다. 작은 크기에서 계정을
알아보게 하는 건 모양보다 색 덩어리다. 피드에 뜬 카드와 프로필이 한
계정으로 읽히려면 같은 주황이어야 한다.

선은 흰색이다. 수치상 대비는 진한 색이 더 높지만, 40px로 줄이면 진한
획이 주황 중간톤에 묻힌다. 실제로 노출되는 크기에서 읽히는 쪽을 골랐다.

플랫폼이 정사각을 원으로 잘라내므로 표식을 안쪽 원에 넉넉히 넣는다.
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
FG = (255, 255, 255)

# 선 두께. 1024에서 76이면 40px로 줄었을 때 약 3px이 된다. 이보다 얇으면
# 축소 과정에서 회색으로 흐려지고, 두꺼우면 곡선의 방향이 뭉개진다.
STROKE = 76

# 표식이 들어갈 자리. 원 안쪽에 여백을 두고 가운데에 앉힌다.
MARK_W = 0.52   # 캔버스 대비 가로 폭
MARK_H = 0.34   # 세로 높이

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


def _points():
    """뼈대를 실제 픽셀 좌표로 옮긴다. 표식 전체를 캔버스 가운데에 맞춘다."""
    w, h = MARK_W * S, MARK_H * S
    x0 = (S - w) / 2
    y0 = (S - h) / 2
    return [(x0 + px * w, y0 + py * h) for px, py in SKELETON]


def render(out_path=OUT):
    img = Image.new("RGB", (S, S), card.ACCENT)
    d = ImageDraw.Draw(img)

    pts = _points()
    d.line(pts, fill=FG, width=STROKE, joint="curve")

    # joint="curve"는 이음매만 둥글게 하고 양 끝은 각진 채로 둔다.
    # 끝에 원을 하나씩 찍어야 선이 잘린 것처럼 보이지 않는다.
    r = STROKE / 2
    for x, y in (pts[0], pts[-1]):
        d.ellipse((x - r, y - r, x + r, y + r), fill=FG)

    return card._save(img, out_path)


def main():
    path = render()
    print(f"프로필 사진 생성: assets/{path.name}")


if __name__ == "__main__":
    main()
