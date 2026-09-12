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
             "note": "+20%부터 MA21, +50%부터 MA50"},
            {"title": "목표가 도달 텔레그램 알림",
             "note": "차트를 안 보고 있어도 옵니다"},
            {"title": "매매일지와 복기 메모",
             "note": "왜 샀는지가 기록으로 남습니다"},
        ],
        "highlight": "전부 무료입니다. 결제 화면 자체가 없습니다.",
        "handle": HANDLE,
        "tagline": TAGLINE,
    },
    # 백테스트 숫자 카드. 불리한 숫자를 그대로 싣는다.
    # data/backtest_stats.json의 minervini_strong 30d 집계이고,
    # 평균 수익률과 개별 종목은 넣지 않는다. 보유 구간에 증자·분할이 낀 종목은
    # entry(원주가)와 exit(수정주가)의 기준이 어긋나 낙폭이 부풀려지는데,
    # 평균과 최악 종목이 그 영향을 가장 크게 받는다. 승률과 건수는 영향이 적다.
    "promo-backtest.jpg": {
        "eyebrow": "조건에 걸린 날 사서 그냥 들고 있었다면",
        "headline": ["888번 돌려보니", "*승률 20.5%*"],
        "rows": [
            {"label": "검증한 매수 신호", "value": "888번"},
            {"label": "30거래일 뒤 이긴 비율", "value": "20.5%"},
            {"label": "20% 넘게 오른 건", "value": "61번"},
            {"label": "10% 넘게 빠진 건", "value": "583번"},
        ],
        "highlight": "손절선도 트레일링도 걸지 않은 숫자입니다. 그래서 -7% 손절 알림을 붙였습니다.",
        "handle": HANDLE,
        "tagline": TAGLINE,
    },
    # 미너비니 탭 설명 카드 2장. 무료 배포글이 아니라 조건을 그대로 까는 글에 쓴다.
    # 숫자와 문구는 scripts/screener.py 를 보고 맞췄다. 스크리너 기준을 바꾸면
    # 이 카드도 같이 고쳐야 한다 — 화면과 카드가 다르면 그게 제일 먼저 들킨다.
    "minervini-trend.jpg": {
        "eyebrow": "미너비니 탭이 보는 것",
        "headline": ["추세 조건 *8개*"],
        # 8개를 다섯 줄로 묶었다. 한 줄에 하나씩 놓으면 글씨가 작아져서
        # 피드에서 안 읽힌다.
        "rows": [
            {"label": "현재가", "value": "50·150·200일선 위"},
            {"label": "이동평균", "value": "50 > 150 > 200"},
            {"label": "200일선", "value": "한 달 전보다 위"},
            {"label": "52주 고점 대비", "value": "25% 이내"},
            {"label": "시장 대비 강도", "value": "전 종목 상위 30%"},
        ],
        "highlight": "8개를 다 통과하면 strict, 6개 이상에 실적까지 붙으면 strong으로 나옵니다.",
        "handle": HANDLE,
        "tagline": TAGLINE,
    },
    "minervini-filter.jpg": {
        "eyebrow": "조건을 보기 전에 아예 빼는 것",
        "headline": ["이건 *처음부터* 뺍니다"],
        "rows": [
            {"label": "관리종목·투자유의", "value": "제외"},
            {"label": "상장 6개월 미만", "value": "제외"},
            {"label": "ETF·스팩·우선주", "value": "제외"},
            {"label": "소형주 5일 +50%", "value": "작전 의심"},
            {"label": "20일 거래대금", "value": "70억 이상 1회"},
        ],
        "highlight": "사람이 고르지 않습니다. 매일 같은 기준으로 자동 계산합니다.",
        "handle": HANDLE,
        "tagline": TAGLINE,
    },
}


def main():
    card.build_cards(CARDS, card.render_promo_card)


if __name__ == "__main__":
    main()
