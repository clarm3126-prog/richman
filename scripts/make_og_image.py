#!/usr/bin/env python3
"""
링크 미리보기(OG) 이미지를 그린다.

카톡·쓰레드·페이스북은 링크를 가로 1.91:1로 잘라서 보여준다. 기존
홍보 카드는 4:5 세로라 그대로 쓰면 위아래가 잘려 브랜드명이 날아간다.
그래서 1200x630 전용 이미지를 따로 둔다.

내용은 일부러 적게 담았다. 미리보기는 손톱만 하게 뜨고 대부분 스쳐
지나가므로, 이름 하나만 확실히 읽히면 된다.

색과 폰트는 홍보 카드와 같은 값을 쓴다 (social/card.py). 카톡에 뜬
미리보기와 인스타에 올라간 카드가 같은 계정으로 읽혀야 한다.

화면을 새로 찍을 필요 없이 이 스크립트만 돌리면 된다.

사용:
  python scripts/make_og_image.py
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).parent))

from social import card  # noqa: E402

OUT = card.ROOT / "assets" / "og.jpg"

# 1200x630은 페이스북·카톡·쓰레드가 공통으로 받는 가로 비율이다.
W, H = 1200, 630
PAD = 72

BRAND = "종목노트"
EYEBROW = "장 마감 후 매일 자동 계산"
TAGLINE = "결제 화면이 없는 종목 스크리너"
CHIPS = ["조건 통과 종목", "목표가·손절선 알림", "매매일지"]
# 미리보기에도 이 문구를 넣는다. 40~70대는 "또 리딩방인가"를 먼저 의심하고,
# 그 판단이 링크를 누르기 전에 끝난다.
FOOTER = "종목 추천이 아닙니다 · stage2.kr"


def render(out_path=OUT):
    img = Image.new("RGB", (W, H), card.P_BG)
    d = ImageDraw.Draw(img)

    f_eyebrow = card._font(True, 30)
    f_brand = card._font(True, 108)
    f_tag = card._font(False, 40)
    f_chip = card._font(True, 28)
    f_foot = card._font(False, 26)

    d.rectangle([0, 0, W, 12], fill=card.P_ACCENT)

    y = PAD + 14
    d.text((PAD, y), EYEBROW, font=f_eyebrow, fill=card.P_ACCENT)
    y += 62

    d.text((PAD, y), BRAND, font=f_brand, fill=card.P_FG)
    y += 142

    d.text((PAD, y), TAGLINE, font=f_tag, fill=card.P_DIM)
    y += 82

    # 칩은 왼쪽부터 이어 붙인다. 폭을 넘으면 그 칩부터 버린다 —
    # 줄을 바꾸면 아래 푸터를 밀어내고, 미리보기에서 잘린다.
    x = PAD
    for text in CHIPS:
        cw = d.textlength(text, font=f_chip) + 44
        if x + cw > W - PAD:
            break
        d.rounded_rectangle(
            [x, y, x + cw, y + 58], radius=14,
            fill=card.P_CHIP, outline=card.P_BORDER, width=2,
        )
        d.text((x + 22, y + 14), text, font=f_chip, fill=card.P_FG)
        x += cw + 14

    foot_y = H - PAD - 30
    d.line(
        [PAD, foot_y - 26, W - PAD, foot_y - 26],
        fill=card.P_BORDER, width=2,
    )
    d.text((PAD, foot_y), FOOTER, font=f_foot, fill=card.P_DIM)

    return card._save(img, out_path)


def main():
    path = render()
    print(f"OG 이미지 생성: assets/{path.name}")


if __name__ == "__main__":
    main()
