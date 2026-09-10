#!/usr/bin/env python3
"""
블로그 매매 기록 카드 생성.

content/posts.yml 의 수동 큐에 들어 있는 글은 인스타에도 올라가는데,
인스타는 이미지가 없으면 게시가 안 된다. 자동 글은 스크리닝 결과로
카드를 그리지만 이 글들은 데이터가 아니라 이야기라서, 소개 카드와
같은 형식(제목 + 구획 + 꼬리말)으로 그린다.

한 번 만들어 저장소에 커밋해두면 발행일까지 그대로 쓰인다.
문구를 고치고 싶으면 아래 CARDS를 수정하고 다시 실행하면 된다.

파일명에 날짜를 넣지 않는다. card.prune_cards()가 앞 10글자를 날짜로
읽어서 오래된 카드를 지우는데, 발행일이 한 달 뒤인 카드가 먼저 지워지면
인스타가 이미지를 못 받아 발행이 실패한다.

사용:
  python scripts/make_blog_cards.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from social import card, config  # noqa: E402

FOOTER = "종목 추천이 아닙니다. 지난 제 매매 기록입니다."

CARDS = {
    "blog-daewon.jpg": {
        "headline": ["대원전선", "11일 만에 정리"],
        "sections": [
            {
                "heading": "매수 · 8,140원",
                "body": "1년 횡보 뒤 거래량 실린 신고가 돌파. 전력 케이블 교체 주기와 "
                        "데이터센터 증설, 구리값 상승이 겹치는 자리.",
            },
            {
                "heading": "매도 · 17,000~18,000원",
                "body": "투자경고 지정. 7,000원대에서 130% 오른 과매수 구간이었고, "
                        "경고 종목은 3~4일 안에 급락하는 경우가 많았다.",
            },
            {
                "heading": "그 뒤",
                "body": "20,300원을 찍고 14,000원대까지 하락. 감보다 규칙이 나았다.",
            },
        ],
        "footer": FOOTER,
    },
    "blog-taesung.jpg": {
        "headline": ["태성", "팔고 나서 400%"],
        "sections": [
            {
                "heading": "매수",
                "body": "저점에서 긴 횡보, 거래량 터지며 상승. 매집 구간이라고 판단.",
            },
            {
                "heading": "매도",
                "body": "200% 오른 뒤 힘이 빠졌다고 보고, 더 좋아 보이는 종목으로 "
                        "갈아타려고 매도.",
            },
            {
                "heading": "무엇이 문제였나",
                "body": "상방이 막 열린 자리였고 조정에도 거래량이 없었다. "
                        "추세가 끊긴 게 아니라 내 인내가 끊겼다.",
            },
        ],
        "footer": FOOTER,
    },
    "blog-soslab.jpg": {
        "headline": ["에스오에스랩", "코인 보고 판 매매"],
        "sections": [
            {
                "heading": "매수",
                "body": "하락 추세를 벗어나 상승 초입이라고 판단.",
            },
            {
                "heading": "매도",
                "body": "지루한 횡보 중 주변의 코인 수익을 보고 1만원 구간에서 청산.",
            },
            {
                "heading": "무엇이 문제였나",
                "body": "판 뒤로 30% 더 올랐다. 매도 버튼을 누르게 한 건 차트가 아니라 "
                        "남의 수익률이었다.",
            },
        ],
        "footer": FOOTER,
    },
    "blog-ligachem.jpg": {
        "headline": ["리가켐바이오", "조금 먹고 나온 매매"],
        "sections": [
            {
                "heading": "매수",
                "body": "긴 횡보 뒤 추세 전환. 지지 자리에서 반등을 확인하고 매수.",
            },
            {
                "heading": "버틴 구간",
                "body": "조정을 맞았지만 거래량이 안 터져 계속 홀딩. 여기까진 원칙대로.",
            },
            {
                "heading": "무엇이 문제였나",
                "body": "매물대에 한 번 막히자 지루함이 겹쳐 급하게 매도. "
                        "거래량도 차트도 안 봤다.",
            },
        ],
        "footer": FOOTER,
    },
    "blog-pskholdings.jpg": {
        "headline": ["피에스케이홀딩스", "아직 보유 중"],
        "sections": [
            {
                "heading": "매수 근거",
                "body": "삼성·하이닉스 납품 반도체 장비주. 전 고점 돌파 후 신고가 갱신, "
                        "3년 연속 순이익 증가.",
            },
            {
                "heading": "달라진 것",
                "body": "실패한 매매를 하나씩 적고 나서야 문제가 보였다. "
                        "추세가 깨져서 판 적은 한 번도 없었다.",
            },
            {
                "heading": "지금",
                "body": "살 때 정한 기준대로 보유 중. 안정적인 수익권.",
            },
        ],
        "footer": "종목 추천이 아닙니다. 2026년 2월 매수 후 보유 중입니다.",
    },
    "blog-principles.jpg": {
        "headline": ["끌려다니지 않으려고", "정한 4원칙"],
        "sections": [
            {
                "heading": "1. 살 때 팔 자리까지 정한다",
                "body": "시나리오 없이 산 종목은 전부 기분대로 팔았다.",
            },
            {
                "heading": "2. 벗어나면 즉시 판다",
                "body": "손절은 규칙이지 그때그때의 판단이 아니다.",
            },
            {
                "heading": "3. 벗어나지 않았으면 안 판다",
                "body": "내가 판 매매는 추세가 깨져서가 아니라 지루해서였다.",
            },
            {
                "heading": "4. 남이 아니라 시나리오를 본다",
                "body": "뉴스·종토방·유튜브·주변 수익률은 매도 사유가 아니다.",
            },
        ],
        "footer": "지난 매매에서 제가 배운 것입니다.",
    },
}


def main():
    cfg = config.load_config()
    link = cfg.get("link") or {}
    brand = link.get("label", "종목노트")

    # url을 비워 넘겨 꼬리말의 주소를 뺀다.
    # 이 카드들은 링크로 유도하는 글이 아니라 매매 기록이라 주소가 필요 없다.
    for name, spec in CARDS.items():
        rel = f"assets/cards/{name}"
        card.render_about_card(spec, config.ROOT / rel, brand=brand, url="")
        print(f"카드 생성: {rel}")


if __name__ == "__main__":
    main()
