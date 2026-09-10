#!/usr/bin/env python3
"""
무료 배포글용 프로모 카드 생성.

종목 카드(어두운 배경)와 달리 밝은 배경을 쓴다. 피드에서 작게 보일 때
리딩방 광고로 읽히지 않게 하려는 것이다.

1장 = 무엇이 나오는지 미리보기, 2장 = 받는 것 5가지 + 무료라는 점.
캐러셀이므로 두 장 모두 4:5(1080x1350)로 그린다.

파일명에 날짜를 넣지 않는다. card.prune_cards()가 앞 10글자를 날짜로 읽어
오래된 카드를 지우기 때문이다.

사용:
  python scripts/make_promo_cards.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from social import card  # noqa: E402

HANDLE = "@stage2.kr"
TAGLINE = "· 결제 화면이 없는 종목 스크리너"

CARDS = {
    "promo-what.jpg": {
        "eyebrow": "종목 코드만 넣으면",
        "headline": ["지금이 살 자리인지", "*재줍니다*"],
        # 실제 종목명은 넣지 않는다. 추천으로 오해받을 여지를 만들지 않는다.
        "rows": [
            {"label": "미너비니 조건", "value": "8개 중 7개 통과"},
            {"label": "52주 고점 대비", "value": "-4.2%"},
            {"label": "손절선", "value": "매수가 -7%"},
            {"label": "트레일링", "value": "+20%부터 MA21"},
            {"label": "목표가 알림", "value": "텔레그램"},
        ],
        "highlight": "종목 추천이 아닙니다. 조건에 걸렸는지만 계산해서 보여줍니다.",
        "handle": HANDLE,
        "tagline": TAGLINE,
    },
    "promo-list.jpg": {
        "eyebrow": "댓글 남기면 보내드릴 것",
        "headline": ["이게 *다* 나옵니다"],
        "items": [
            {"title": "미너비니 8개 조건 통과 개수",
             "note": "추세·수급·실적을 한 번에 계산"},
            {"title": "52주 고점 대비 지금 위치",
             "note": "얼마나 눌린 자리인지 바로 보임"},
            {"title": "손절선과 트레일링 기준",
             "note": "+20%부터 MA21, +50%부터 MA5"},
            {"title": "목표가 도달 텔레그램 알림",
             "note": "차트를 안 보고 있어도 옵니다"},
            {"title": "매매일지와 복기 메모",
             "note": "왜 샀는지가 기록으로 남습니다"},
        ],
        "highlight": "전부 무료입니다. 결제 화면 자체가 없습니다.",
        "handle": HANDLE,
        "tagline": TAGLINE,
    },
}


def main():
    for name, spec in CARDS.items():
        out = card.CARD_DIR / name
        card.render_promo_card(spec, out)
        print(f"만듦: {out.relative_to(card.ROOT)}")


if __name__ == "__main__":
    main()
