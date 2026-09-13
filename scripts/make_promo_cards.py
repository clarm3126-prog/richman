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

HANDLE = "종목노트"
TAGLINE = "· 매일 조건에 걸린 종목만 · 결제 화면이 없습니다"

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
    # data/backtest_stats.json의 minervini_strong 30d 집계다. 숫자가 바뀌면
    # 이 카드와 09-22 글을 같이 고쳐야 한다 - 화면과 다르면 바로 들킨다.
    #
    # 평균만 놓으면 "형편없다"로 읽힌다. 같은 기간 지수와 규칙을 지킨 경우를
    # 나란히 둬야 숫자가 제 뜻으로 읽힌다.
    "promo-backtest.jpg": {
        "eyebrow": "조건에 걸린 날 사서 30거래일",
        "headline": ["손절 하나로", "*-15% → -6.7%*"],
        "rows": [
            {"label": "검증한 종목", "value": "77개 · 888건"},
            {"label": "그냥 들고 있었을 때", "value": "평균 -15.2%"},
            {"label": "같은 기간 코스피", "value": "평균 -10.9%"},
            {"label": "손절 규칙을 지켰을 때", "value": "평균 -6.7%", "mark": True},
            {"label": "제일 크게 깨진 한 건", "value": "-60.9% → -22.9%"},
        ],
        "highlight": "승률은 오히려 20.5%에서 13.9%로 떨어집니다. 자주 잘리는 대신 크게 깨지지 않습니다.",
        "handle": HANDLE,
        "tagline": TAGLINE,
    },
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
    # 두 탭 차이 설명 카드. 실제로 받은 질문("모멘텀은 미너비니 통과 종목
    # 중에서 고르나요?")에서 나왔다. 답이 "아니오"라는 게 카드의 전부다.
    "minervini-vs-momentum.jpg": {
        "eyebrow": "받은 질문 — 두 탭은 무슨 관계인가요",
        "headline": ["같은 종목이 아니라", "*다른 시점*입니다"],
        "rows": [
            {"label": "미너비니", "value": "추세가 만들어진 뒤"},
            {"label": "모멘텀", "value": "200일선을 막 뚫은 때"},
            {"label": "미너비니가 보는 것", "value": "정배열 · 고점 25% 이내"},
            {"label": "모멘텀이 보는 것", "value": "VCP · 거래량 · 피벗"},
            {"label": "둘 다 걸리면", "value": "합류로 따로 표시"},
        ],
        "highlight": "모멘텀 탭이 미너비니 통과 종목 중에서 고르는 게 아닙니다. 각각 전 종목을 따로 훑습니다.",
        "handle": HANDLE,
        "tagline": TAGLINE,
    },
    # 손절선 민감도 카드. scripts/stop_sensitivity.py 출력을 옮긴 것이다.
    # 숫자를 다시 뽑으면 이 카드와 10-02 글을 같이 고쳐야 한다.
    "stop-levels.jpg": {
        "eyebrow": "조건 통과 888건에 대입",
        "headline": ["손절선을 몇 %로", "*잡아야 하나*"],
        "rows": [
            {"label": "-5%", "value": "평균 -6.1% · 승률 11.9%"},
            # mark를 준 줄에만 왼쪽 막대가 붙는다. 내가 쓰는 값이 어느 것인지
            # 글을 안 읽어도 보이게 하려는 것이다.
            {"label": "-7%", "value": "평균 -6.7% · 승률 13.9%", "mark": True},
            {"label": "-10%", "value": "평균 -7.8% · 승률 17.5%"},
            {"label": "-15%", "value": "평균 -9.6% · 승률 21.5%"},
            {"label": "안 걸면", "value": "평균 -13.6% · 최악 -60.9%"},
        ],
        "highlight": "좁을수록 평균이 낫고 승률은 떨어집니다. 다만 이 넉 달은 하락장이었습니다.",
        "handle": HANDLE,
        "tagline": TAGLINE,
    },
    # 텔레그램 연결 안내 카드. 글만으로는 단계가 안 읽혀서 만든다.
    # 3번(시작 버튼)에서 제일 많이 막히므로 그 줄에 이유를 붙여둔다.
    "telegram-guide.jpg": {
        "eyebrow": "목표가 알림 받는 법",
        "headline": ["텔레그램 연결 *4단계*"],
        "items": [
            {"title": "사이트에서 구글 로그인",
             "note": "오른쪽 위 사람 모양 버튼"},
            {"title": "관심 탭에서 '알림' 버튼",
             "note": "6자리 코드가 뜹니다"},
            {"title": "봇 대화창에서 [시작] 누르기",
             "note": "이걸 안 누르면 봇이 메시지를 못 받습니다"},
            {"title": "복사된 코드를 붙여넣어 전송",
             "note": "'연결 완료'가 오면 끝입니다"},
        ],
        "highlight": "종목에 목표가를 걸어두셔야 알림이 옵니다. 손절 알림은 매입가를 넣은 종목에만 갑니다.",
        "handle": HANDLE,
        "tagline": TAGLINE,
    },
    # 언제 알림이 오는지. "손절은 어떻게 표시되나요"라는 질문에서 나왔다.
    # 맨 아래 줄이 핵심이다 - 매입가를 안 넣어서 알림이 안 온다는 문의가
    # 연결 다음으로 많다. 값은 exit_signals.py의 실제 조건과 같아야 한다.
    "alert-rules.jpg": {
        "eyebrow": "받은 질문 — 손절은 어떻게 표시되나요",
        "headline": ["화면이 아니라", "*텔레그램*으로 옵니다"],
        "rows": [
            {"label": "매입가에서 -7%", "value": "손절 알림", "mark": True},
            {"label": "+20% 넘긴 뒤 21일선 이탈", "value": "매도 신호"},
            {"label": "+50% 넘긴 뒤 50일선 이탈", "value": "매도 신호"},
            {"label": "목표가 도달", "value": "도달 알림"},
        ],
        "highlight": "관심 종목에 매입가를 넣으셔야 손절·트레일링이 동작합니다. 목표가 알림은 매입가 없이도 옵니다.",
        "handle": HANDLE,
        "tagline": TAGLINE,
    },
}


def main():
    card.build_cards(CARDS, card.render_promo_card)


if __name__ == "__main__":
    main()
