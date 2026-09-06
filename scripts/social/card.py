#!/usr/bin/env python3
"""
인스타그램용 카드 이미지 생성.

인스타는 텍스트만으로 게시할 수 없어서 스크리닝 결과를 이미지로 그린다.
만든 이미지는 저장소에 커밋하고 GitHub Pages가 서빙하는 URL을 인스타에 넘긴다.

크기는 1080x1350 (4:5) 이다.
인스타 프로필 그리드가 4:5 세로로 바뀌어서, 1:1 정사각으로 올리면
그리드 썸네일에서 좌우가 잘린다. 4:5로 올리면 피드와 그리드 모두
잘림 없이 그대로 보인다.

형식은 JPEG. 인스타 게시 API는 JPEG만 받는다 (PNG는 컨테이너가 ERROR).

썸네일은 아주 작게 표시되므로, 맨 위에 큰 헤드라인 두 줄을 두어
클릭하기 전에도 무슨 글인지 알 수 있게 한다.

한글 폰트는 우분투(fonts-nanum)와 윈도우(맑은 고딕) 양쪽 경로를 모두 찾는다.
"""
import glob
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
CARD_DIR = ROOT / "assets" / "cards"

W, H = 1080, 1350
PAD = 76

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


def _save(img, out_path):
    """확장자에 맞춰 저장한다.

    글자가 많은 카드라 크로마 서브샘플링을 끄고(4:4:4) 품질을 높게 잡는다.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.suffix.lower() in (".jpg", ".jpeg"):
        img.save(out_path, "JPEG", quality=92, optimize=True, subsampling=0)
    else:
        img.save(out_path, "PNG", optimize=True)
    return out_path


def _fit(draw, text, font, max_w):
    """폭을 넘으면 말줄임표로 자른다."""
    if draw.textlength(text, font=font) <= max_w:
        return text
    while text and draw.textlength(text + "…", font=font) > max_w:
        text = text[:-1]
    return text + "…"


def _wrap(draw, text, font, max_w):
    """폭에 맞춰 줄바꿈. 공백으로 먼저 나누고, 한 덩어리가 너무 길면 글자 단위로 자른다."""
    lines, cur = [], ""
    for word in (text or "").split(" "):
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
            continue
        if cur:
            lines.append(cur)
        while draw.textlength(word, font=font) > max_w:
            cut = len(word)
            while cut > 1 and draw.textlength(word[:cut], font=font) > max_w:
                cut -= 1
            lines.append(word[:cut])
            word = word[cut:]
        cur = word
    if cur:
        lines.append(cur)
    return lines


def _headline(d, lines, inner_w, top):
    """썸네일에서도 읽히도록 큰 글씨로 헤드라인을 얹는다.

    폭에 맞을 때까지 글자 크기를 줄인다. 줄이 늘어나면 크기도 함께 낮춘다.
    """
    size = 96 if len(lines) <= 2 else 78
    while size > 48:
        f = _font(True, size)
        if all(d.textlength(l, font=f) <= inner_w for l in lines):
            break
        size -= 4
    f = _font(True, size)
    lh = int(size * 1.18)
    y = top
    for line in lines:
        d.text((PAD, y), _fit(d, line, f, inner_w), font=f, fill=FG)
        y += lh
    return y


def render_card(post, out_path, brand="종목노트", url=""):
    """compose.py가 만든 post dict를 카드 이미지로 그린다."""
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    f_badge = _font(True, 30)
    f_sub = _font(False, 30)
    f_rank = _font(True, 30)
    f_name = _font(True, 42)
    f_note = _font(False, 26)
    f_val = _font(True, 38)
    f_delta = _font(True, 28)
    f_foot = _font(False, 26)
    f_brand = _font(True, 30)

    inner_w = W - PAD * 2
    d.rectangle([0, 0, W, 10], fill=ACCENT)

    y = PAD + 14

    # 분류 배지 - 무슨 종류의 글인지 한눈에
    badge = post.get("badge")
    if badge:
        tw = d.textlength(badge, font=f_badge)
        d.rounded_rectangle([PAD, y, PAD + tw + 40, y + 52], radius=26, fill=ACCENT)
        d.text((PAD + 20, y + 10), badge, font=f_badge, fill=(255, 255, 255))
        y += 76

    # 헤드라인 - 썸네일에서 읽히는 유일한 부분
    y = _headline(d, post.get("headline") or [post.get("title", "")], inner_w, y)
    y += 12

    if post.get("subtitle"):
        d.text((PAD, y), _fit(d, post["subtitle"], f_sub, inner_w), font=f_sub, fill=FG_DIM)
        y += 54

    y += 20

    items = post.get("items", [])[:5]
    foot_y = H - PAD - 60
    row_h = min(132, max(96, (foot_y - 40 - y) // max(1, len(items))))

    for i, it in enumerate(items, 1):
        top = y
        d.rounded_rectangle([PAD, top, W - PAD, top + row_h - 14], radius=18, fill=PANEL)

        bx = PAD + 22
        d.rounded_rectangle([bx, top + 30, bx + 46, top + 76], radius=12, fill=ACCENT)
        d.text((bx + 16, top + 38), str(i), font=f_rank, fill=(255, 255, 255))

        val = it.get("value") or ""
        delta = it.get("delta")
        delta_txt = f"{delta:+.1f}%" if isinstance(delta, (int, float)) else ""
        right = W - PAD - 26
        val_w = d.textlength(val, font=f_val)
        d.text((right - val_w, top + 26), val, font=f_val, fill=FG)
        if delta_txt:
            dw = d.textlength(delta_txt, font=f_delta)
            d.text(
                (right - dw, top + 70),
                delta_txt,
                font=f_delta,
                fill=UP if delta >= 0 else DOWN,
            )

        text_x = bx + 70
        text_w = (right - max(val_w, 120) - 40) - text_x
        d.text(
            (text_x, top + 24),
            _fit(d, it.get("name", ""), f_name, text_w),
            font=f_name,
            fill=FG,
        )
        if it.get("note"):
            d.text(
                (text_x, top + 74),
                _fit(d, it["note"], f_note, text_w),
                font=f_note,
                fill=FG_DIM,
            )
        y += row_h

    d.line([PAD, foot_y - 26, W - PAD, foot_y - 26], fill=(45, 50, 60), width=2)
    d.text((PAD, foot_y), brand, font=f_brand, fill=ACCENT)
    if url:
        short = url.replace("https://", "").rstrip("/")
        w = d.textlength(short, font=f_foot)
        d.text((W - PAD - w, foot_y + 4), short, font=f_foot, fill=FG_DIM)

    return _save(img, out_path)


def render_about_card(about, out_path, brand="종목노트", url=""):
    """캐러셀 2번째 장에 고정으로 붙는 소개 카드.

    매일 바뀌는 종목 카드와 달리 내용이 고정이라, 설정을 바꾸지 않는 한
    매번 같은 이미지가 나온다. 같은 바이트면 git이 변경으로 보지 않는다.
    캐러셀은 첫 장의 비율로 잘리므로 종목 카드와 같은 4:5를 쓴다.
    """
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    f_head = _font(True, 40)
    f_body = _font(False, 29)
    f_foot = _font(False, 26)
    f_brand = _font(True, 30)

    d.rectangle([0, 0, W, 10], fill=ACCENT)

    inner_w = W - PAD * 2
    y = _headline(d, about.get("headline") or [about.get("title", "")], inner_w, PAD + 20)
    y += 40

    sections = about.get("sections") or []
    foot_y = H - PAD - 60
    avail = foot_y - 48 - y
    gap = 22
    text_w = inner_w - 56

    blocks = [[s.get("heading", ""), _wrap(d, s.get("body", ""), f_body, text_w)] for s in sections]

    def total(bs):
        return sum(60 + len(b[1]) * 42 + 46 for b in bs) + gap * max(0, len(bs) - 1)

    while blocks and total(blocks) > avail:
        longest = max(blocks, key=lambda b: len(b[1]))
        if len(longest[1]) <= 1:
            blocks.pop()
        else:
            longest[1] = longest[1][:-1]
            longest[1][-1] = longest[1][-1].rstrip() + "…"

    for heading, body_lines in blocks:
        h = 60 + len(body_lines) * 42 + 46
        d.rounded_rectangle([PAD, y, W - PAD, y + h - 8], radius=18, fill=PANEL)
        d.text((PAD + 28, y + 26), _fit(d, heading, f_head, text_w), font=f_head, fill=ACCENT)
        ty = y + 86
        for line in body_lines:
            d.text((PAD + 28, ty), line, font=f_body, fill=FG)
            ty += 42
        y += h + gap

    if about.get("footer"):
        # 구분선(foot_y - 26) 위로 여유를 두고 아래에서부터 쌓는다
        flines = _wrap(d, about["footer"], f_body, inner_w)[:2]
        start = foot_y - 50 - len(flines) * 40
        for i, line in enumerate(flines):
            d.text((PAD, start + i * 40), line, font=f_body, fill=FG_DIM)

    d.line([PAD, foot_y - 26, W - PAD, foot_y - 26], fill=(45, 50, 60), width=2)
    d.text((PAD, foot_y), brand, font=f_brand, fill=ACCENT)
    if url:
        short = url.replace("https://", "").rstrip("/")
        w = d.textlength(short, font=f_foot)
        d.text((W - PAD - w, foot_y + 4), short, font=f_foot, fill=FG_DIM)

    return _save(img, out_path)


def prune_cards(keep_days=60):
    """오래된 카드를 지운다. 공개 저장소가 이미지로 계속 불어나는 걸 막는다."""
    from datetime import date, timedelta

    if not CARD_DIR.exists():
        return 0
    cutoff = (date.today() - timedelta(days=keep_days)).isoformat()
    removed = 0
    for p in list(CARD_DIR.glob("*.jpg")) + list(CARD_DIR.glob("*.png")):
        stamp = p.name[:10]
        if len(stamp) == 10 and stamp[4] == "-" and stamp < cutoff:
            p.unlink()
            removed += 1
    return removed
