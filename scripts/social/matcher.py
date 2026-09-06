#!/usr/bin/env python3
"""
댓글 -> 답장/DM 문구 매칭.

content/social.yml의 rules를 위에서부터 훑어 첫 번째로 걸리는 규칙 하나만 쓴다.
아무것도 안 걸리면 default를 쓰고, default도 비어 있으면 그 댓글은 넘어간다.
skip 목록에 걸리는 댓글에는 아무 반응도 하지 않는다.
"""
import re

_SPACE = re.compile(r"\s+")


def normalize(text):
    t = (text or "").lower()
    return t, _SPACE.sub("", t)


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

    if _hit(engage_cfg.get("skip_keywords"), spaced, tight):
        return None

    for rule in engage_cfg.get("rules") or []:
        if _hit(rule.get("keywords"), spaced, tight):
            return (rule.get("name") or "rule", rule)

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
