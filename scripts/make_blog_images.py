#!/usr/bin/env python3
"""네이버 블로그 꾸밈 이미지를 그린다.

블로그에 들어가는 자리가 몇 군데 있고 크기가 제각각이다. 같은 그림을
늘려 쓰면 글자가 뭉개지거나 잘리므로 자리마다 따로 그린다.

  타이틀      966x300   PC 블로그 홈 맨 위 띠
  모바일커버  1280x720  휴대폰에서 블로그 들어가면 처음 보이는 그림
  프로필      이미 assets/avatar.png 에 있다 (make_avatar.py)

**타이틀은 오른쪽 400px 가 비어야 한다.** 네이버가 그 자리에 블로그
이름을 겹쳐 찍는다. 글자를 가운데 두면 이름과 겹쳐 둘 다 못 읽는다.
실제로 겹쳐 보고 정한 값이다.

색과 폰트는 홍보 카드와 같은 값을 쓴다 (social/card.py). 쓰레드에 뜬
카드와 블로그를 같은 계정으로 읽히게 하려면 같은 주황이어야 한다.

출력: assets/blog/title.png, assets/blog/cover.png

사용:
  python scripts/make_blog_images.py
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).parent))

from social import card  # noqa: E402

OUT_DIR = card.ROOT / "assets" / "blog"

BRAND = "종목노트"
EYEBROW = "장 마감 후 매일 자동 계산"
TAGLINE = "매일 조건에 걸린 종목만"
FOOTER = "종목 추천이 아닙니다 · stage2.kr"

# 네이버가 블로그 이름을 겹쳐 찍는 자리. 여기는 비워 둔다.
NAME_ZONE = 400

# 배너 아래 구분선. 바탕과 배너가 같은 색이라 이게 없으면 경계가 사라진다.
DIVIDER = 3
DIVIDER_COLOR = (214, 208, 197)


def _mark(d, cx, cy, scale, color):
    """아바타와 같은 표식 — 바닥을 다지다 위로 뚫고 나가는 선.

    프로필 사진과 배너에 같은 모양이 있어야 한 계정으로 읽힌다.
    """
    pts = [(-1.00, 0.18), (-0.55, 0.10), (-0.30, 0.34), (-0.05, -0.05),
           (0.30, -0.20), (0.95, -0.78)]
    xy = [(cx + x * scale, cy + y * scale) for x, y in pts]
    d.line(xy, fill=color, width=max(6, int(scale * 0.13)), joint="curve")


def title_image():
    """PC 블로그 홈 맨 위 띠. 966x300."""
    W, H = 966, 300
    img = Image.new("RGB", (W, H), card.P_BG)
    d = ImageDraw.Draw(img)

    d.rectangle([0, 0, W, 8], fill=card.P_ACCENT)

    f_eyebrow = card._font(True, 24)
    f_brand = card._font(True, 76)
    f_tag = card._font(False, 30)
    f_foot = card._font(False, 20)

    pad = 56
    y = pad + 6
    d.text((pad, y), EYEBROW, font=f_eyebrow, fill=card.P_ACCENT)
    y += 48
    d.text((pad, y), BRAND, font=f_brand, fill=card.P_FG)
    y += 98
    d.text((pad, y), TAGLINE, font=f_tag, fill=card.P_DIM)

    d.text((pad, H - pad + 8), FOOTER, font=f_foot, fill=card.P_DIM)

    # 표식은 이름이 겹칠 자리를 피해 오른쪽 끝에 둔다.
    _mark(d, W - NAME_ZONE // 2, H // 2, 62, card.P_ACCENT)

    # 아래 구분선. 블로그 바탕을 배너와 같은 크림색으로 맞추고 나면 배너가
    # 어디서 끝나는지 보이지 않는다. 배경색을 맞추는 것과 경계가 보이는 것은
    # 따로 챙겨야 한다.
    #
    # 주황으로 그으면 위 띠와 두 줄이 되어 배너가 상자처럼 갇힌다. 크림보다
    # 조금 어두운 정도면 경계로만 읽히고 시선을 끌지 않는다.
    d.rectangle([0, H - DIVIDER, W, H], fill=DIVIDER_COLOR)
    return img


def cover_image():
    """휴대폰에서 블로그 들어가면 처음 보이는 그림. 1280x720.

    휴대폰은 위아래가 잘리는 일이 잦아 글자를 가운데에 모은다.
    """
    W, H = 1280, 720
    img = Image.new("RGB", (W, H), card.P_BG)
    d = ImageDraw.Draw(img)

    d.rectangle([0, 0, W, 12], fill=card.P_ACCENT)

    f_eyebrow = card._font(True, 34)
    f_brand = card._font(True, 132)
    f_tag = card._font(False, 44)
    f_foot = card._font(False, 28)

    def center(text, font, y, fill):
        w = d.textlength(text, font=font)
        d.text(((W - w) / 2, y), text, font=font, fill=fill)

    _mark(d, W // 2, 188, 92, card.P_ACCENT)

    y = 268
    center(EYEBROW, f_eyebrow, y, card.P_ACCENT)
    y += 62
    center(BRAND, f_brand, y, card.P_FG)
    y += 172
    center(TAGLINE, f_tag, y, card.P_DIM)

    center(FOOTER, f_foot, H - 78, card.P_DIM)
    return img


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, fn in (("title", title_image), ("cover", cover_image)):
        img = fn()
        path = OUT_DIR / f"{name}.png"
        img.save(path)
        print(f"생성: assets/blog/{name}.png  {img.size[0]}x{img.size[1]}")


if __name__ == "__main__":
    main()
