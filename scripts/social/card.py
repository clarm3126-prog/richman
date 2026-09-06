#!/usr/bin/env python3
"""
인스타그램용 카드 이미지 생성.

인스타는 텍스트만으로 게시할 수 없어서, 스크리닝 결과를 1080x1080 PNG로 그린다.
만든 PNG는 저장소에 커밋하고 GitHub Pages가 서빙하는 URL을 인스타에 넘긴다.

한글 폰트는 우분투(fonts-nanum)와 윈도우(맑은 고딕) 양쪽 경로를 모두 찾는다.
"""
import glob
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
CARD_DIR = ROOT / "assets" / "cards"

SIZE = 1080
PAD = 72

BG = (20, 22, 27)
PANEL = (30, 34, 42)
ACCENT = (124, 58, 237)
FG = (233, 236, 241)
FG_DIM = (150, 158, 170)
UP = (239, 68, 68)      # 국내 관행: 상승 빨강
DOWN = (59, 130, 246)   # 하락 파랑

BOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
    "/usr/share/fonts/truetype/nanum/NanumBarunGothicBold.ttf",
    "C:/Windows/Fonts/malgunbd.ttf",
]
REGULAR_CANDIDATES = [
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf",
    "C:/Windows/Fonts/malgun.ttf",
]
GLOB_FALLBACKS = [
    "/usr/share/fonts/**/Nanum*.ttf",
    "/usr/share/fonts/**/NotoSansCJK*.otf",
    "/usr/share/fonts/**/*CJK*.ttc",
]


def _find_font(candidates):
    for p in candidates:
        if Path(p).exists():
            return p
    for pattern in GLOB_FALLBACKS:
        hits = sorted(glob.glob(pattern, recursive=True))
        if hits:
            return hits[0]
    return None


def _font(bold, size):
    path = _find_font(BOLD_CANDIDATES if bold else REGULAR_CANDIDATES)
    if not path:
        raise RuntimeError(
            "한글 폰트를 찾지 못했습니다. 워크플로우에서 fonts-nanum 설치가 필요합니다."
        )
    return ImageFont.truetype(path, size)


def _fit(draw, text, font, max_w):
    """폭을 넘으면 말줄임표로 자른다."""
    if draw.textlength(text, font=font) <= max_w:
        return text
    while text and draw.textlength(text + "…", font=font) > max_w:
        text = text[:-1]
    return text + "…"


def render_card(post, out_path, brand="종목노트", url=""):
    """compose.py가 만든 post dict를 카드 PNG로 그린다."""
    img = Image.new("RGB", (SIZE, SIZE), BG)
    d = ImageDraw.Draw(img)

    f_title = _font(True, 58)
    f_sub = _font(False, 30)
    f_rank = _font(True, 30)
    f_name = _font(True, 42)
    f_note = _font(False, 26)
    f_val = _font(True, 36)
    f_delta = _font(True, 28)
    f_foot = _font(False, 26)
    f_brand = _font(True, 30)

    # 상단 액센트 바
    d.rectangle([0, 0, SIZE, 10], fill=ACCENT)

    y = PAD + 18
    d.text((PAD, y), _fit(d, post["title"], f_title, SIZE - PAD * 2), font=f_title, fill=FG)
    y += 76
    if post.get("subtitle"):
        d.text(
            (PAD, y),
            _fit(d, post["subtitle"], f_sub, SIZE - PAD * 2),
            font=f_sub,
            fill=FG_DIM,
        )
        y += 52

    y += 18
    items = post.get("items", [])[:5]
    row_h = 118
    for i, it in enumerate(items, 1):
        top = y
        d.rounded_rectangle([PAD, top, SIZE - PAD, top + row_h - 14], radius=18, fill=PANEL)

        # 순번 배지
        bx = PAD + 22
        d.rounded_rectangle([bx, top + 30, bx + 46, top + 76], radius=12, fill=ACCENT)
        d.text((bx + 16, top + 38), str(i), font=f_rank, fill=(255, 255, 255))

        # 오른쪽 값부터 자리 계산
        val = it.get("value") or ""
        delta = it.get("delta")
        delta_txt = f"{delta:+.1f}%" if isinstance(delta, (int, float)) else ""
        right = SIZE - PAD - 26
        val_w = d.textlength(val, font=f_val)
        d.text((right - val_w, top + 26), val, font=f_val, fill=FG)
        if delta_txt:
            dw = d.textlength(delta_txt, font=f_delta)
            d.text(
                (right - dw, top + 68),
                delta_txt,
                font=f_delta,
                fill=UP if delta >= 0 else DOWN,
            )

        # 왼쪽 이름/설명
        text_x = bx + 70
        text_w = (right - max(val_w, 120) - 40) - text_x
        d.text(
            (text_x, top + 22),
            _fit(d, it.get("name", ""), f_name, text_w),
            font=f_name,
            fill=FG,
        )
        if it.get("note"):
            d.text(
                (text_x, top + 72),
                _fit(d, it["note"], f_note, text_w),
                font=f_note,
                fill=FG_DIM,
            )
        y += row_h

    # 하단
    foot_y = SIZE - PAD - 66
    d.line([PAD, foot_y - 26, SIZE - PAD, foot_y - 26], fill=(45, 50, 60), width=2)
    d.text((PAD, foot_y), brand, font=f_brand, fill=ACCENT)
    if url:
        short = url.replace("https://", "").rstrip("/")
        w = d.textlength(short, font=f_foot)
        d.text((SIZE - PAD - w, foot_y + 4), short, font=f_foot, fill=FG_DIM)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG", optimize=True)
    return out_path


def prune_cards(keep_days=60):
    """오래된 카드 PNG를 지운다. 공개 저장소가 이미지로 계속 불어나는 걸 막는다."""
    from datetime import date, timedelta

    if not CARD_DIR.exists():
        return 0
    cutoff = (date.today() - timedelta(days=keep_days)).isoformat()
    removed = 0
    for p in CARD_DIR.glob("*.png"):
        stamp = p.name[:10]
        if len(stamp) == 10 and stamp[4] == "-" and stamp < cutoff:
            p.unlink()
            removed += 1
    return removed
