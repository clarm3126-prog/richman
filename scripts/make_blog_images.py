#!/usr/bin/env python3
"""네이버 블로그 꾸밈 이미지를 그린다.

블로그에 들어가는 자리가 몇 군데 있고 크기가 제각각이다. 같은 그림을
늘려 쓰면 글자가 뭉개지거나 잘리므로 자리마다 따로 그린다.

  타이틀      966x300   PC 블로그 홈 맨 위 띠
  앱 커버     1200x1200 블로그 앱에서 처음 보이는 그림 (글자 없음)
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
    """블로그 앱 커버. 1200x1200.

    **글자를 넣지 않는다.** 앱이 커버 위에 블로그명·소개·방문수·프로필을
    직접 얹는다. 여기에 '종목노트'를 또 그리면 같은 글자가 두 번 나오고
    서로 겹친다. 실제로 그렇게 나왔다.

    바탕을 주황으로 둔다. 앱이 커버 위에 흰 글자를 얹고 어두운 막을
    씌우는데, 크림 바탕에서는 그 흰 글자가 묻힌다. 주황이면 막이 씌워져도
    흰 글자가 또렷하다.

    표식은 위쪽에만 둔다. 아래 절반은 앱이 글자와 버튼으로 채우는 자리라
    비워야 한다.

    정사각으로 만드는 이유는 기기마다 잘리는 비율이 달라서다. 가로로 길게
    만들면 좁은 화면에서 좌우가 잘려 표식이 날아간다. 정사각에 여백을
    넉넉히 두면 어느 쪽으로 잘려도 표식이 남는다.
    """
    S = 1200
    img = Image.new("RGB", (S, S), card.ACCENT)
    d = ImageDraw.Draw(img)
    # 위에서 30% 지점. 아래는 앱 글자 자리로 비워 둔다.
    _mark(d, S // 2, int(S * 0.30), 215, (255, 255, 255))
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
