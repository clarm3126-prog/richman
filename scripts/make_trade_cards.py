#!/usr/bin/env python3
"""매매 기록 카드를 만든다. 글자만 있던 카드에 차트를 넣는다.

지금 매매 기록 카드(blog-taesung.jpg 등)는 글자만 있다. "200% 오른 자리에서
팔았는데 400% 더 갔다"를 글로만 읽으면 그림이 안 그려진다. 같은 이야기를
차트 위에 ▲▼ 로 찍으면 한눈에 들어온다.

큰 계정들이 종목 글에 항상 차트를 붙이는 이유이기도 하다. 쓰레드에서
이미지가 붙으면 머무는 시간이 늘고, 그게 순위 신호 중 하나("스크롤로
지나칠 확률")를 직접 낮춘다.

트레이딩뷰와는 붙일 수 없다. 공개 API 가 없고, 위젯은 남의 차트라 내
매수·매도를 못 그린다. 대신 시세는 스크리너가 쓰는 네이버 차트 API 를
그대로 쓰고, 그림은 여기서 직접 그린다.

**금액은 그리지 않는다.** 퍼센트만 쓴다. 계좌 크기가 드러나면 글의 성격이
달라진다.

  python scripts/make_trade_cards.py            content/trades.yml 전부
  python scripts/make_trade_cards.py taesung    하나만
"""
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from social import card as C  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

CHART_API = "https://m.stock.naver.com/front-api/external/chart/domestic/info"

UP = (239, 68, 68)      # 매수 — 한국 관습대로 빨강
DOWN = (59, 130, 246)   # 매도 — 파랑
LINE = (203, 213, 225)
AREA = (44, 49, 60)
GRID = (52, 58, 70)


def fetch_daily(code, start, end):
    """일봉을 받는다. screener.py 가 쓰는 것과 같은 곳이다."""
    url = CHART_API + "?" + urllib.parse.urlencode({
        "symbol": code, "requestType": "1",
        "startTime": start, "endTime": end, "timeframe": "day",
    })
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(req, timeout=30).read().decode("utf-8")
    rows = json.loads(raw.replace("'", '"'))
    # 첫 줄은 머리글이다
    return [{"d": r[0], "c": r[4], "v": r[5]} for r in rows[1:] if r[4]]


def _at(rows, date):
    """그 날짜 봉. 휴장이면 바로 다음 거래일."""
    for i, r in enumerate(rows):
        if r["d"] >= date:
            return i, r
    return len(rows) - 1, rows[-1]


def draw_chart(d, rows, box, buy_i, sell_i):
    """종가 선과 매수·매도 표시. 값 축은 그리지 않는다 — 금액을 숨기려는 것."""
    x0, y0, x1, y1 = box
    C._panel(d, y0, y1, AREA, border=GRID, radius=18)

    lo = min(r["c"] for r in rows)
    hi = max(r["c"] for r in rows)
    span = (hi - lo) or 1
    pad = 46
    px0, px1 = x0 + pad, x1 - pad
    py0, py1 = y0 + 54, y1 - 62

    def X(i):
        return px0 + (px1 - px0) * i / max(len(rows) - 1, 1)

    def Y(v):
        return py1 - (py1 - py0) * (v - lo) / span

    pts = [(X(i), Y(r["c"])) for i, r in enumerate(rows)]
    d.polygon(pts + [(px1, py1), (px0, py1)], fill=(36, 41, 51))
    d.line(pts, fill=LINE, width=4, joint="curve")

    f_tag = C._font(True, 30)
    f_small = C._font(False, 24)

    def mark(i, color, label, above):
        x, y = X(i), Y(rows[i]["c"])
        r = 11
        d.ellipse([x - r, y - r, x + r, y + r], fill=color)
        d.ellipse([x - r - 5, y - r - 5, x + r + 5, y + r + 5], outline=color, width=3)
        tw = d.textlength(label, font=f_tag)
        bx = min(max(x - tw / 2 - 16, x0 + 16), x1 - tw - 48)
        by = y - 68 if above else y + 30
        # 바닥 근처에서 산 경우 아래로 붙이면 판을 뚫고 날짜와 겹친다.
        if by + 46 > py1 + 10:
            by = y - 68
        if by < y0 + 8:
            by = y + 30
        d.rounded_rectangle([bx, by, bx + tw + 32, by + 46], radius=14, fill=color)
        d.text((bx + 16, by + 7), label, font=f_tag, fill=(255, 255, 255))

    mark(buy_i, UP, "매수", False)
    mark(sell_i, DOWN, "매도", True)

    # 기간만 적는다. 값은 안 적는다.
    def ymd(s):
        return "%s.%s.%s" % (s[2:4], s[4:6], s[6:8])

    d.text((px0, y1 - 46), ymd(rows[0]["d"]), font=f_small, fill=C.FG_DIM)
    tw = d.textlength(ymd(rows[-1]["d"]), font=f_small)
    d.text((px1 - tw, y1 - 46), ymd(rows[-1]["d"]), font=f_small, fill=C.FG_DIM)


def render(spec, out_path):
    rows = fetch_daily(spec["code"], spec["from"], spec["to"])
    if len(rows) < 10:
        raise SystemExit("%s: 봉이 %d개뿐입니다" % (spec["name"], len(rows)))
    buy_i, buy = _at(rows, spec["buy"])
    sell_i, sell = _at(rows, spec["sell"])
    gain = (sell["c"] / buy["c"] - 1) * 100
    after = (max(r["c"] for r in rows[sell_i:]) / sell["c"] - 1) * 100

    img = Image.new("RGB", (C.W, C.H), C.BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, C.W, 10], fill=C.ACCENT)

    inner = C.W - C.PAD * 2
    y = C.PAD + 6
    y = C._headline(d, spec["headline"], inner, y, accent=C.ACCENT, min_size=54)
    y += 10

    f_sub = C._font(False, 30)
    sub = "보유 %d거래일 · 판 뒤 고점까지 %+.0f%%" % (sell_i - buy_i, after)
    d.text((C.PAD, y), sub, font=f_sub, fill=C.FG_DIM)
    y += 56

    # 아래 칸을 먼저 재고 남는 만큼을 차트에 준다. 글이 길어지면 차트가
    # 줄어들 뿐, 바닥을 뚫고 면책 문구와 겹치지 않는다.
    f_head = C._font(True, 34)
    f_body = C._font(False, 30)
    blocks = []
    for sec in spec["sections"]:
        lines = C._wrap(d, sec["body"], f_body, inner - 56)
        blocks.append((sec["heading"], lines, 78 + len(lines) * 42))

    foot_top = C.H - 170
    avail = foot_top - y - sum(h + 12 for _, _, h in blocks) - 30
    chart_h = max(300, min(470, avail))

    draw_chart(d, rows, (C.PAD, y, C.W - C.PAD, y + chart_h), buy_i, sell_i)
    y += chart_h + 30

    for heading, lines, h in blocks:
        C._panel(d, y, y + h - 14, C.PANEL, border=C.BORDER, radius=18)
        d.text((C.PAD + 28, y + 20), heading, font=f_head, fill=C.ACCENT)
        ty = y + 66
        for l in lines:
            d.text((C.PAD + 28, ty), l, font=f_body, fill=C.FG)
            ty += 42
        y += h + 12

    f_foot = C._font(False, 26)
    d.text((C.PAD, C.H - 150), "종목 추천이 아닙니다. 지난 제 매매 기록입니다.",
           font=f_foot, fill=C.FG_DIM)
    d.line([C.PAD, C.H - 106, C.W - C.PAD, C.H - 106], fill=C.BORDER, width=2)
    d.text((C.PAD, C.H - 86), "종목노트", font=C._font(True, 30), fill=C.ACCENT)

    C._save(img, out_path)
    print("  %s  매수 %s → 매도 %s (%+.0f%%)" % (out_path.name, buy["d"], sell["d"], gain))


def main():
    import yaml
    src = ROOT / "content" / "trades.yml"
    if not src.exists():
        raise SystemExit("content/trades.yml 이 없습니다")
    specs = yaml.safe_load(src.read_text(encoding="utf-8"))["trades"]
    only = sys.argv[1] if len(sys.argv) > 1 else None
    out_dir = ROOT / "assets" / "cards"
    for s in specs:
        if only and only not in s["file"]:
            continue
        if not s.get("buy") or not s.get("sell"):
            print("  %s — 매수·매도 날짜가 비어 있어 건너뜁니다" % s["name"])
            continue
        render(s, out_dir / s["file"])


if __name__ == "__main__":
    main()
