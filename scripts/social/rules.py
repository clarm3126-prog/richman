#!/usr/bin/env python3
"""종목노트 글쓰기 규칙 — 재볼 수 있는 것만 규칙으로 둔다.

숫자는 쓰레드에서 실제로 재서 나왔다. 좋아요가 많은 글 여러 편과 팔로워가
많은 계정 넷(12만·5.3만·1만·524)의 글을 훑어 줄 길이를 세어 본 값이다.

  장투대장 (12만)    한 줄 9~15자
  더양봉맨 (5.3만)   한 줄 13~14자
  프로브   (1만)     한 줄 21~24자

팔로워가 많을수록 짧았다. 길이 자체가 반응을 만든다기보다, 짧게 끊어야
휴대폰에서 한 줄이 한 줄로 보이기 때문으로 읽힌다. 40~70대가 보는 글이라
이 효과가 더 크다.

**규칙은 검사할 수 있는 것만 넣는다.** "첫 줄이 후킹이어야 한다" 같은 건
사람이 판단할 몫이라 여기 두지 않는다. 대신 check()가 잡아내는 것들은
전부 기계가 셀 수 있는 항목이다.
"""
import re

# 주소만 있는 줄. 길이 규칙에서 뺀다.
_URL_ONLY = re.compile(r"^(https?://)?[\w.-]+\.[a-z]{2,}(/\S*)?$", re.I)

# 한 줄 길이. 평균은 권고, 최장은 상한이다.
LINE_AVG_TARGET = 15
LINE_MAX = 25

# 글 전체 길이. 쓰레드 본문 상한이 500자다. 여유를 두고 자른다.
TEXT_MAX = 480
LINES_MIN, LINES_MAX = 5, 20

# 본문에 쓰면 안 되는 것들.
#
# 종목 이름을 본문에 쓰면 추천으로 읽힌다. 사진에 이름이 보이는 것과
# 글에서 짚는 것은 받아들이는 쪽에서 전혀 다르다.
#
# 영어 약자는 화면에서 이미 걷어냈다. 글에만 남으면 화면과 어긋난다.
#
# '보장' 은 낱말만 막으면 안 된다. "오른다는 보장은 없습니다" 같은
# 면책 문장에도 걸려서 고칠 것이 없는데 경고가 울린다. 약속하는 꼴만
# 막는다.
BANNED_TERMS = [
    "MA50", "MA150", "MA200", "MA20", "MA21", "RS70", "52w",
    "EPS", "OPM", "VCP", "Pivot", "피벗", "Higher Lows",
    "strict", "strong",
    "추천드립니다", "매수하세요", "사세요", "지금이 기회",
    "확실합니다", "수익률 보장", "보장합니다", "보장해", "보장드",
]

# 글 끝에 붙이는 유도 문구. 본문에 링크를 넣지 않는다.
#
# 쓰레드는 밖으로 내보내는 링크를 반기지 않는다. 조사한 계정 넷 모두
# 본문에 링크 대신 댓글을 유도했다.
#
# 다만 "댓글에 X 남기면 Y 드려요"는 Meta가 미끼로 지목한 형태다. 한 단어
# 유도는 한 단어 답글을 만들고, 그런 답글은 분류기에서 할인된다. 판정은
# 계정이 아니라 글 단위라 한 편으로 막히지는 않지만, 전부 그 형태면 읽는
# 사람이 얕게 반응하는 법을 배운다.
#
# 실제로 그랬다. 좋아요 10에 댓글 65~70 — 다른 계정과 정반대 비율이다.
# 대화가 일어난 게 아니라 한 단어가 65번 찍힌 것이다.
#
# 그래서 질문을 앞에 두고 링크 안내를 뒤로 물린다. 답글이 문장이 되면
# 가장 센 신호(답글 참여)에 제대로 걸린다. 앞줄 질문은 글마다 달라야
# 하므로 여기 두지 않는다.
CTA = "링크가 필요하시면 '노트'라고 적어주세요."

# 면책. 조사한 계정은 소개란에 박아두고 본문에서는 뺀다. 우리는 글마다
# 숫자를 들고 오므로 본문에도 남긴다.
DISCLAIMER = "종목 추천이 아닙니다.\n조건에 걸렸는지만 계산합니다."


def wrap(text, limit=LINE_MAX):
    """긴 줄을 뜻이 끊기는 자리에서 나눈다.

    글자 수만 보고 자르면 조사나 숫자 가운데가 갈린다. 문장부호 → 조사 →
    공백 순으로 자를 자리를 찾고, 어디서도 못 찾으면 그냥 둔다. 억지로
    자르느니 긴 줄 하나를 남기고 check()가 잡게 하는 편이 낫다.
    """
    out = []
    for line in text.split("\n"):
        line = line.rstrip()
        while len(line) > limit:
            # 상한 안에서 자를 수 있는 가장 뒤쪽 자리를 고른다. 상한을 넘는
            # 자리를 고르면 자르고도 여전히 긴 줄이 남는다.
            cut = 0
            for pat in (r"[.!?·]\s", r"(?:니다|어요|세요|고요|구요|는데|지만|라서|으로|에서)\s", r"\s"):
                for m in re.finditer(pat, line):
                    if m.end() <= limit:
                        cut = max(cut, m.end())
                if cut:
                    break  # 더 센 기준에서 찾았으면 약한 기준은 보지 않는다
            if not cut:
                # 상한 안에 자를 자리가 없다. 억지로 자르지 않고 남긴다.
                # check()가 잡아 주므로 사람이 고쳐 쓰면 된다.
                break
            out.append(line[:cut].rstrip())
            line = line[cut:].lstrip()
        out.append(line)
    return "\n".join(out)


def check(text, prose=True):
    """규칙 위반을 모아 돌려준다. 빈 목록이면 통과다.

    prose=False 면 줄 모양 규칙(줄 수, 한 줄 길이, 한 줄 평균)을 건너뛴다.

    자동 생성 목록 글은 "1. HD현대마린솔루션 247,000원 (+7.6%)" 처럼 한 줄이
    길 수밖에 없다. 종목 이름과 가격이 정하는 길이라 사람이 줄일 수 없는데,
    거기에 산문 규칙을 들이대면 매일 같은 경고가 울리고 결국 아무도 안 본다.
    경고는 고칠 수 있는 것만 울려야 읽힌다.

    글자 수 상한과 쓰면 안 되는 말은 글의 형식과 무관하므로 항상 본다.
    상한을 넘으면 쓰레드에서 잘려 나가고, 영어 약자는 화면 라벨과 어긋난다.
    둘 다 자동 글에서도 그대로 사고다.
    """
    problems = []
    lines = [l for l in text.split("\n") if l.strip()]

    if not lines:
        return ["본문이 비었습니다"]
    if len(text) > TEXT_MAX:
        problems.append(f"글자 수 초과 ({len(text)}자, 최대 {TEXT_MAX})")
    if not prose:
        for term in BANNED_TERMS:
            if term in text:
                problems.append(f"쓰면 안 되는 말: {term}")
        return problems

    if len(lines) < LINES_MIN:
        problems.append(f"줄이 너무 적습니다 ({len(lines)}줄, 최소 {LINES_MIN})")
    if len(lines) > LINES_MAX:
        problems.append(f"줄이 너무 많습니다 ({len(lines)}줄, 최대 {LINES_MAX})")

    # 주소 한 줄은 길이에서 뺀다. 자를 수 없는 줄이라 경고해도 고칠
    # 방법이 없다. 고칠 수 없는 경고는 결국 안 읽히게 만든다.
    measured = [l for l in lines if not _URL_ONLY.match(l.strip())]
    over = [l for l in measured if len(l) > LINE_MAX]
    if over:
        problems.append(f"{LINE_MAX}자 넘는 줄 {len(over)}개 (가장 긴 줄 {max(len(l) for l in over)}자)")

    avg = sum(len(l) for l in measured) / max(1, len(measured))
    if avg > LINE_AVG_TARGET + 5:
        problems.append(f"한 줄 평균이 깁니다 ({avg:.0f}자, 목표 {LINE_AVG_TARGET}자)")

    for term in BANNED_TERMS:
        if term in text:
            problems.append(f"쓰면 안 되는 말: {term}")

    return problems


def stats(text):
    lines = [l for l in text.split("\n") if l.strip()]
    if not lines:
        return {}
    lens = [len(l) for l in lines]
    return {
        "줄": len(lines),
        "한줄평균": round(sum(lens) / len(lens)),
        "최장": max(lens),
        "글자수": len(text),
    }
