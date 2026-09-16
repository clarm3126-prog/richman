#!/usr/bin/env python3
"""
댓글 -> 답장/DM 문구 매칭.

content/social.yml의 rules를 위에서부터 훑어 첫 번째로 걸리는 규칙 하나만 쓴다.
아무것도 안 걸리면 default를 쓰고, default도 비어 있으면 그 댓글은 넘어간다.
skip 목록에 걸리는 댓글에는 아무 반응도 하지 않는다.

질문에는 봇이 답하지 않는다. 물어본 사람한테 "댓글 감사해요!"가 나가면
사장님이 답할 것을 놓친다(2026-09-17 실제로 그랬다).

막는 쪽이 기본이다. 질문에도 답해도 되는 규칙만 social.yml 에서
answers_question: true 로 열어준다. 지금은 link_request 하나뿐이다 -
그건 "답"이 아니라 달라고 한 것을 건네주는 일이라서다.

허용 목록으로 뒤집은 이유: 처음에는 인사(thanks·default)만 막았는데,
"손절은 어떻게 잡으시나요?" 가 how_it_works 의 '어떻게' 에 걸려
종목 고르는 기준을 읊는 엉뚱한 답이 나갔다. 질문 규칙의 낱말은
넓을 수밖에 없어서, 막을 것을 고르는 방식으로는 계속 샌다.
"""
import re

_SPACE = re.compile(r"\s+")

# 물음표가 제일 확실하지만, 한국어 댓글은 물음표를 자주 뺀다.
# "~나요" "~까요" "~는지" 로 끝나면 물음표가 없어도 질문으로 본다.
_QMARK = re.compile(r"[?？]")
_QTAIL = re.compile(r"(나요|까요|은가요|인가요|ㄴ가요|는지|을까|ㄹ까)[\s.!~ㅎㅋ,…]*$")
# 물음표도 없고 어미도 안 맞지만 묻는 말인 경우. "되는지 궁금하네요"
_QWORD = re.compile(r"궁금|여쭤|여쭙|질문")

# 질문에도 답해도 되는 규칙. social.yml 의 answers_question: true 와 같은 뜻이고,
# 설정에서 빠뜨려도 동작하도록 여기에도 적어 둔다.
ANSWERS_QUESTION = {"link_request"}

# 거르기 낱말 가운데 뜻이 겹쳐 오탐을 내는 것만 정규식으로 따로 본다.
# '신고' 는 계정 주제인 '신고가' 를 삼킨다. 뒤에 '가' 가 오면 통과시킨다.
# ('신고합니다' '신고할게요' 는 그대로 걸린다)
_SKIP_EXCEPT = {"신고": re.compile(r"신고(?!가)")}


def is_question(text):
    """댓글이 질문인가."""
    t = (text or "").strip()
    if not t:
        return False
    return bool(_QMARK.search(t) or _QTAIL.search(t) or _QWORD.search(t))


def is_skipped(text, engage_cfg):
    """거르기 낱말에 걸리는 댓글인가.

    시비·홍보성 댓글은 "답을 기다리는 질문" 목록에도 올리지 않는다.
    "리딩방이죠?" 가 물음표를 달고 온다고 사장님이 답할 질문은 아니다.
    """
    spaced, _ = normalize(text)
    return _skip_hit((engage_cfg or {}).get("skip_keywords"), spaced)


def quiet_on_question(engage_cfg):
    """질문에 일반 답장을 막는 설정이 켜져 있나. 기본은 켜짐."""
    return bool((engage_cfg or {}).get("no_generic_reply_on_question", True))


def normalize(text):
    t = (text or "").lower()
    return t, _SPACE.sub("", t)


def _skip_hit(keywords, spaced):
    """거르기 전용 매칭.

    찾기(_hit)와 달리 **공백을 지우지 않은 원문**에서 본다.
    공백을 지우고 substring 으로 보면 '회사 기준' 이 '사기' 가 되고
    '조사 기간' 도 '사기' 가 되어, 링크 달라는 댓글이 통째로 버려졌다.

    낱말 하나(3자 이하)는 공백을 끼우지 않고 그대로 찾는다.
    '디엠주세요' 같은 말토막은 사람이 띄어 쓰므로 사이 공백을 허용한다.
    """
    for kw in keywords or []:
        k = (kw or "").lower().strip()
        if not k:
            continue
        rx = _SKIP_EXCEPT.get(k)
        if rx is not None:
            if rx.search(spaced):
                return True
        elif len(k) <= 3:
            if k in spaced:
                return True
        else:
            if re.search(r"[ ]*".join(map(re.escape, k)), spaced):
                return True
    return False


def _hit(keywords, spaced, tight):
    for kw in keywords or []:
        k = (kw or "").lower().strip()
        if not k:
            continue
        if k in spaced or _SPACE.sub("", k) in tight:
            return True
    return False


def match(text, engage_cfg):
    """(rule_name, rule_dict) 또는 None."""
    spaced, tight = normalize(text)

    if _skip_hit(engage_cfg.get("skip_keywords"), spaced):
        return None

    # 질문이면 내용 없는 인사는 달지 않는다. 답이 정해진 규칙은 그대로 답한다.
    quiet = quiet_on_question(engage_cfg) and is_question(text)

    for rule in engage_cfg.get("rules") or []:
        if _hit(rule.get("keywords"), spaced, tight):
            name = rule.get("name") or "rule"
            if quiet and not (rule.get("answers_question") or name in ANSWERS_QUESTION):
                return None
            return (name, rule)

    if quiet:
        return None

    default = engage_cfg.get("default") or {}
    if default.get("reply") or default.get("dm"):
        return ("default", default)
    return None


def pick(rule, field, platform):
    """플랫폼별 문구가 있으면 그걸, 없으면 공통 문구를 쓴다."""
    return rule.get(f"{field}_{platform}") or rule.get(field)


def fill(template, user="", link="", brand=""):
    """문구 안의 자리표시자를 채운다. 여러 줄을 |로 구분해 무작위로 고른다."""
    if not template:
        return ""
    text = template
    if isinstance(template, list):
        import random

        text = random.choice(template)
    handle = user if not user or user.startswith("@") else f"@{user}"
    return (
        text.replace("{user}", handle)
        .replace("{username}", user or "")
        .replace("{link}", link or "")
        .replace("{brand}", brand or "")
        .strip()
    )
