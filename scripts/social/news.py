#!/usr/bin/env python3
"""
네이버 금융 뉴스 제목 수집.

기사 본문은 가져오지 않는다. 제목과 매체 이름만 쓰고 글에 출처를 밝힌다.
언론사 기사 제목을 출처와 함께 인용하는 건 보도 인용 관행 안이지만,
본문을 옮기는 건 저작권 침해라 아예 하지 않는다.

주소는 붙이지 않는다. 쓰레드는 본문에 링크가 있으면 도달이 크게 깎인다.

카페나 커뮤니티 글은 다루지 않는다. 회원들이 쓴 저작물이고 무단 전재가
금지돼 있어서, 요약해서 올리는 것도 침해다.

--- 어떤 기사를 고르는가 ---

최신순으로 앞에서 세 개를 집으면 시황과 상관없는 기사가 섞인다.
그래서 제목에 점수를 매기고 기준을 넘은 것만 쓴다.

  1. 걸러내기   낚시성·비시황 기사는 제목만 보고 뺀다 (DROP)
  2. 시간       오래된 기사는 뺀다 (within_hours)
  3. 점수       시장 전체를 말하는 단어에 가중치를 준다 (CORE)
  4. 오늘 연결  오늘 실제로 움직인 테마·종목 이름이 제목에 있으면 가산점
  5. 매체 분산  같은 매체는 하나만
  6. 미달 처리  기준을 넘은 게 2개 미만이면 아예 안 쓴다

6번이 핵심이다. 억지로 세 개를 채우느니 뉴스 항목을 통째로 빼는 게 낫다.
"""
import re
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

BASE = "https://finance.naver.com/news/news_list.naver"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; StageTwoNews/1.0)",
    "Referer": "https://finance.naver.com/",
}

KST = timezone(timedelta(hours=9))

# 네이버 금융 뉴스 섹션
SECTIONS = {
    "world": 403,   # 해외증시
    "market": 401,  # 시황·전망
}

# 제목에 이게 있으면 쓰지 않는다.
# 통신사 자동 생성 기사, 종목 추천성 기사, 시황과 무관한 기사를 막는다.
DROP = (
    "[그래픽]", "[표]", "[부고]", "[인사]", "[포토]", "[영상]", "[게시판]",
    "[특징주]", "[클릭", "[신간]", "[사설]", "[기고]",
    "상한가", "하한가", "급등주", "유망주", "추천주", "수익률 1위",
    "오늘의 운세", "무료", "이벤트", "세미나", "설명회", "리딩",
)

# 시장 전체를 말하는 단어일수록 점수가 높다.
CORE = {
    "코스피": 3, "코스닥": 3, "증시": 3, "지수": 2, "마감": 2, "개장": 2,
    "외국인": 3, "기관": 2, "수급": 2, "순매수": 2, "순매도": 2,
    "환율": 3, "원달러": 3, "원·달러": 3, "금리": 3, "국채": 2, "채권": 2,
    "연준": 3, "FOMC": 3, "파월": 2, "물가": 2, "CPI": 3, "고용": 2,
    "나스닥": 3, "다우": 3, "S&P": 3, "뉴욕증시": 3, "뉴욕 증시": 3,
    "유가": 2, "반도체": 2, "실적": 2, "전망": 1,
}

MIN_SCORE = 3
MIN_ITEMS = 2


def _clean(title):
    """따옴표 종류를 통일하고 공백을 정리한다."""
    t = re.sub(r"\s+", " ", title or "").strip()
    return t.replace("‘", "'").replace("’", "'").replace("“", '"').replace("”", '"')


def _parse_when(text):
    """'2026-09-10 14:29' -> datetime(KST). 못 읽으면 None."""
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})", text or "")
    if not m:
        return None
    y, mo, d, h, mi = (int(x) for x in m.groups())
    try:
        return datetime(y, mo, d, h, mi, tzinfo=KST)
    except ValueError:
        return None


def score(title, boost=()):
    """제목 점수. 높을수록 시황 글에 어울린다."""
    if title.startswith(DROP) or any(w in title for w in DROP):
        return -1
    total = sum(weight for word, weight in CORE.items() if word in title)
    # 오늘 실제로 움직인 테마·종목 이름이 제목에 있으면 크게 올린다.
    # 그날 시장과 이어지는 기사가 시황 글에는 가장 잘 붙는다.
    for word in boost:
        if word and len(word) >= 2 and word in title:
            total += 4
            break
    return total


def _fetch(section, timeout):
    sid3 = SECTIONS.get(section)
    if not sid3:
        return None
    params = {
        "mode": "LSS3D",
        "section_id": 101,
        "section_id2": 258,
        "section_id3": sid3,
    }
    try:
        r = requests.get(BASE, params=params, headers=HEADERS, timeout=timeout)
        if r.status_code != 200:
            return None
        # 네이버 금융은 아직 EUC-KR이다
        r.encoding = "euc-kr"
        return BeautifulSoup(r.text, "html.parser")
    except Exception:
        return None


def headlines(section="world", limit=3, within_hours=24, boost=(), timeout=10):
    """[{title, press, score}] 목록. 기준 미달이면 빈 목록.

    뉴스를 못 가져와도 글 자체는 나가야 하므로 예외를 밖으로 던지지 않는다.
    """
    soup = _fetch(section, timeout)
    if soup is None:
        return []

    now = datetime.now(KST)
    cutoff = now - timedelta(hours=within_hours) if within_hours else None

    scored, seen = [], set()
    for subject in soup.select(".articleSubject"):
        a = subject.select_one("a")
        if not a:
            continue
        title = _clean(a.get("title") or a.get_text(strip=True))
        if not title:
            continue
        # 같은 사건을 여러 매체가 쓰면 제목이 거의 같다. 앞부분으로 걸러낸다.
        key = title[:14]
        if key in seen:
            continue
        seen.add(key)

        summary = subject.find_next(class_="articleSummary")
        press = summary.select_one(".press") if summary else None
        wdate = summary.select_one(".wdate") if summary else None

        when = _parse_when(wdate.get_text() if wdate else "")
        if cutoff and when and when < cutoff:
            continue

        pts = score(title, boost)
        if pts < MIN_SCORE:
            continue

        scored.append(
            {
                "title": title,
                "press": _clean(press.get_text()) if press else "",
                "score": pts,
                "when": when,
            }
        )

    scored.sort(key=lambda x: (-x["score"], -(x["when"].timestamp() if x["when"] else 0)))

    # 같은 매체가 여러 줄을 차지하면 한쪽으로 치우쳐 보인다
    picked, used_press = [], set()
    for item in scored:
        if item["press"] and item["press"] in used_press:
            continue
        used_press.add(item["press"])
        picked.append(item)
        if len(picked) >= limit:
            break

    # 억지로 채우느니 뉴스 자체를 빼는 게 낫다
    return picked if len(picked) >= MIN_ITEMS else []


def as_lines(items, bullet="·"):
    """글 본문에 넣을 줄 목록으로 바꾼다."""
    lines = []
    for it in items:
        press = f" — {it['press']}" if it.get("press") else ""
        lines.append(f"{bullet} {it['title']}{press}")
    return lines


def market_boost(market, limit=8):
    """오늘 움직인 테마·신고가 종목 이름. 점수 가산에 쓴다."""
    words = []
    themes = sorted(
        (t for t in (market or {}).get("naver_themes") or []
         if isinstance(t.get("change"), (int, float))),
        key=lambda t: -t["change"],
    )
    for t in themes[:3]:
        words.append(t.get("name", ""))
        for s in (t.get("stocks") or [])[:2]:
            words.append(s.get("name", ""))
    return [w for w in words if w][:limit]
