#!/usr/bin/env python3
"""
사이트 화면 스크린샷을 세로 카드로 다시 짜준다.

화면을 그대로 찍으면 가로로 아주 길다. 종목 카드가 화면 너비를 다 쓰는데
정작 내용은 왼쪽에 몰려 있고 점수만 오른쪽 끝에 있어서, 가운데가 통째로
비기 때문이다. 그대로 올리면 피드에서 축소돼 뱃지 글씨가 뭉갠다.

그래서 **비어 있는 가운데 세로 구간을 찾아 잘라내고** 왼쪽 내용과 오른쪽
점수를 붙인다. 그다음 4:5 캔버스에 얹고 위아래에 설명 띠를 둘러 카드로
만든다. 잘라낸 자리에는 점선을 그어 "여기가 생략됐다"를 표시한다 —
아무 표시 없이 이어 붙이면 화면을 조작한 것처럼 보인다.

화면을 새로 찍으면 이 스크립트만 다시 돌리면 된다.

사용:
  python scripts/make_tab_cards.py
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).parent))

from social import card  # noqa: E402

# 스크린샷 원본을 두는 곳. 결과와 섞이지 않게 따로 둔다.
SRC_DIR = card.ROOT / "assets" / "screenshots"

HANDLE = "종목노트"
TAGLINE = "· 결제 화면이 없는 종목 스크리너"

# 잘라낼 빈 구간을 고를 때 쓰는 값.
# 기준 열과 색이 이만큼 안에서 같으면 '빈 열'로 본다. JPEG 압축 때문에
# 완전히 같은 값이 나오지는 않아서 여유를 준다.
SAME_TOL = 12
# 이보다 좁은 빈 구간은 그냥 둔다. 뱃지 사이 여백까지 잘라내면 안 된다.
MIN_GAP = 200
# 잘라낸 자리에 남길 너비 (점선이 들어갈 자리)
SEAM_W = 56

CARDS = {
    "tab-minervini.jpg": {
        "src": "tab-minervini.png",
        "eyebrow": "미너비니 탭",
        "headline": "초록은 통과한 조건, 회색은 못 넘은 조건",
    },
    "tab-momentum.jpg": {
        "src": "tab-momentum.png",
        "eyebrow": "모멘텀 탭",
        "headline": "200일선을 막 뚫은 종목만 모읍니다",
    },
}


def _empty_columns(img, tol=SAME_TOL):
    """기준 열과 사실상 같은 열을 찾는다.

    기준은 가로 한가운데 열이다. 이 배치에서 한가운데는 항상 비어 있다.
    내용이 있는 열은 글자나 뱃지 때문에 기준과 색이 어긋난다.
    """
    px = img.load()
    w, h = img.size
    ref_x = w // 2
    ref = [px[ref_x, y] for y in range(h)]

    empty = []
    for x in range(w):
        same = True
        for y in range(0, h, 3):  # 3픽셀 걸러 봐도 충분하고 훨씬 빠르다
            a, b = px[x, y], ref[y]
            if abs(a[0] - b[0]) > tol or abs(a[1] - b[1]) > tol or abs(a[2] - b[2]) > tol:
                same = False
                break
        empty.append(same)
    return empty


def _widest_run(flags):
    """True가 가장 길게 이어지는 구간 (start, end). 없으면 None."""
    best = cur = None
    for i, f in enumerate(flags):
        if f:
            cur = (cur[0], i + 1) if cur else (i, i + 1)
            if not best or (cur[1] - cur[0]) > (best[1] - best[0]):
                best = cur
        else:
            cur = None
    return best


def squeeze(img):
    """가운데 빈 구간을 잘라내고 양쪽을 붙인다. 자른 폭을 함께 돌려준다."""
    run = _widest_run(_empty_columns(img))
    if not run or (run[1] - run[0]) < MIN_GAP:
        return img, 0, None

    x0, x1 = run
    left = img.crop((0, 0, x0, img.height))
    right = img.crop((x1, 0, img.width, img.height))

    out = Image.new("RGB", (left.width + SEAM_W + right.width, img.height))
    out.paste(left, (0, 0))
    out.paste(right, (left.width + SEAM_W, 0))

    # 이어붙인 자리는 '확실히 빈 열'의 색으로 메운다. 바로 옆 열을 쓰면
    # 그 자리가 뱃지 안쪽일 때 뱃지 색이 옆으로 늘어나 얼룩처럼 보인다.
    # 가운데 열은 어차피 비어 있으므로 그 행의 배경색 그 자체다.
    px = img.load()
    ref_x = img.width // 2
    fill = Image.new("RGB", (SEAM_W, img.height))
    fp = fill.load()
    for y in range(img.height):
        c = px[ref_x, y][:3]
        for x in range(SEAM_W):
            fp[x, y] = c
    out.paste(fill, (left.width, 0))

    return out, (x1 - x0), left.width + SEAM_W // 2


def render(spec, out_path):
    src = SRC_DIR / spec["src"]
    if not src.exists():
        raise SystemExit(
            f"원본 스크린샷이 없습니다: {src}\n"
            f"  화면을 찍어 이 경로에 두고 다시 실행하세요."
        )

    shot = Image.open(src).convert("RGB")
    shot, cut, seam_x = squeeze(shot)

    img = Image.new("RGB", (card.W, card.H), card.BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, card.W, 12], fill=card.ACCENT)

    f_eyebrow = card._font(True, 34)
    f_head = card._font(True, 46)
    f_note = card._font(False, 26)
    f_handle = card._font(True, 30)
    f_tag = card._font(False, 27)

    inner_w = card.W - card.PAD * 2
    y = card.PAD + 16
    d.text((card.PAD, y), spec["eyebrow"], font=f_eyebrow, fill=card.ACCENT)
    y += 58

    for line in card._wrap(d, spec["headline"], f_head, inner_w):
        d.text((card.PAD, y), line, font=f_head, fill=card.FG)
        y += 60
    y += 30

    # 화면은 제목 바로 밑에 붙인다. 세로 가운데에 두면 제목과 화면 사이가
    # 휑하게 비어서 두 장을 붙여 놓은 것처럼 보인다.
    # 좌우 여백은 본문(PAD)보다 좁게 준다. 뱃지 글씨가 한 줄이라도 커야 한다.
    side = 40
    box_w = card.W - side * 2
    foot_y = card.H - card.PAD - 56
    avail_h = foot_y - 90 - y
    scale = min(box_w / shot.width, avail_h / shot.height)
    sw, sh = int(shot.width * scale), int(shot.height * scale)
    shot = shot.resize((sw, sh), Image.LANCZOS)
    sx = (card.W - sw) // 2
    sy = y
    img.paste(shot, (sx, sy))
    d.rounded_rectangle([sx - 2, sy - 2, sx + sw + 1, sy + sh + 1],
                        radius=10, outline=(58, 65, 79), width=2)

    # 잘라낸 자리 표시. 화면을 손봤다는 사실을 숨기지 않는다.
    if cut and seam_x is not None:
        gx = sx + int(seam_x * scale)
        for gy in range(sy, sy + sh, 18):
            d.line([gx, gy, gx, min(gy + 9, sy + sh)], fill=(120, 128, 142), width=2)
        note = f"가운데 빈 자리 {cut}px를 접었습니다"
        nw = d.textlength(note, font=f_note)
        d.text((card.W - card.PAD - nw, sy + sh + 14), note, font=f_note, fill=card.FG_DIM)

    d.line([card.PAD, foot_y - 24, card.W - card.PAD, foot_y - 24], fill=(45, 50, 60), width=2)
    d.text((card.PAD, foot_y), HANDLE, font=f_handle, fill=card.FG)
    hw = d.textlength(HANDLE, font=f_handle)
    d.text((card.PAD + hw + 14, foot_y + 4), TAGLINE, font=f_tag, fill=card.FG_DIM)

    img.save(out_path, "JPEG", quality=92)


def main():
    for name, spec in CARDS.items():
        out = card.CARD_DIR / name
        render(spec, out)
        print(f"카드 생성: assets/cards/{name}")


if __name__ == "__main__":
    main()
