"""위험/가점 신호 정규식 정본 (docs/career-plan.md §신호 규칙).

이 파일이 사용자의 판정 기준을 코드로 옮긴 곳이다. 패턴이 조용히 깨지면 위험 공고가
적합으로 넘어가므로 **tests/test_rules.py로 실제 검증되는 유일한 모듈**이다.

패턴은 두 등급으로 나뉜다.
  hard — 완화 없이 무조건 `위험` 강제. 근무형태가 문구로 확정되는 신호(3교대, 24*365).
  soft — 같은 신호명이라도 `구축 조직 동거`가 함께 잡히면 강제하지 않고 LLM 판단에 맡긴다.
         (운영+구축 겸업 공고를 통째로 버리지 않기 위함)
매칭은 대소문자 무시 + 공백 정규화된 텍스트에 대해 수행한다(signals.normalize).
"""
from __future__ import annotations
import re

# --- 위험 신호: 신호명 -> {"hard": [...], "soft": [...]} ---
RISK: dict[str, dict[str, list[str]]] = {
    "관제 전담": {
        # 근무형태가 문구로 확정 → 완화 없음
        "hard": [
            r"3\s?교대", r"4조\s?3교대", r"2조\s?2교대", r"교대\s?근무", r"교대근무",
            r"24[\s*×xX/·]?365", r"24\s?시간\s?365",
        ],
        # 직무 성격 신호 → '구축 조직 동거'와 공존하면 완화
        "soft": [
            r"관제", r"\bNOC\b", r"모니터링\s?요원", r"상주\s?관제", r"야간\s?당직",
        ],
    },
    "소기업·1인": {
        # 문구가 명시적으로 1인 체제를 말하는 경우만. (사원수 자체는 API가 주지 않는다 —
        # docs/career-plan.md §P1 알려진 한계. 여기서 못 잡으면 LLM이 '판단 불가'를 반환한다.)
        "hard": [
            r"1인\s?전산", r"전산\s?담당\s?1\s?명", r"혼자\s?전산", r"단독\s?운영",
        ],
        "soft": [
            r"대표\s?직속", r"사원\s?수\s?[1-9]\s?명", r"직원\s?[1-9]\s?명",
        ],
    },
}

# --- 가점 신호: 신호명 -> [패턴] ---
POSITIVE: dict[str, list[str]] = {
    "구축 조직 동거": [
        r"구축", r"\bSI\b", r"설계.{0,10}구축", r"인프라\s?구축",
        r"이관", r"마이그레이션", r"\bPoC\b", r"신규\s?도입",
    ],
    "주간 중심": [
        r"주\s?5일", r"09:?00.{0,10}18:?00", r"상시\s?주간",
        r"교대\s?없", r"주간\s?근무", r"주간\s?고정",
    ],
}

# 완화 규칙: 위험 신호명 -> 이 가점 신호가 함께 잡히면 soft 매치는 위험을 강제하지 않는다.
MITIGATED_BY: dict[str, str] = {
    "관제 전담": "구축 조직 동거",
}

# 노션 Signals multi_select 옵션 정본(위험 + 가점)
RISK_NAMES: list[str] = list(RISK.keys())
POSITIVE_NAMES: list[str] = list(POSITIVE.keys())
ALL_SIGNAL_NAMES: list[str] = RISK_NAMES + POSITIVE_NAMES


def _compile(patterns: list[str]) -> list[re.Pattern[str]]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]


# 미리 컴파일 (건×패턴 수십 회 매칭이므로 매번 컴파일하지 않는다)
RISK_C: dict[str, dict[str, list[re.Pattern[str]]]] = {
    name: {grade: _compile(pats) for grade, pats in grades.items()}
    for name, grades in RISK.items()
}
POSITIVE_C: dict[str, list[re.Pattern[str]]] = {
    name: _compile(pats) for name, pats in POSITIVE.items()
}
