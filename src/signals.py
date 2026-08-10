"""규칙 기반 신호 추출 — **코드가 확정하는 부분**.

LLM보다 먼저 돌고, LLM 프롬프트에 힌트로 주입되며, LLM이 다르게 판단해도
`forced`가 True인 건은 evaluator가 verdict를 `위험`으로 되돌린다.
(daily-recall generator.py:146이 category/difficulty를 컨텍스트 값으로 덮어쓰는 것과 같은 패턴)
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field

from config import rules
from src.sources.base import JobPosting

_WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    """공백 정규화. 대소문자는 패턴 쪽 IGNORECASE가 처리한다."""
    return _WS.sub(" ", text or "").strip()


@dataclass
class SignalResult:
    risk: list[str] = field(default_factory=list)        # 잡힌 위험 신호명
    positive: list[str] = field(default_factory=list)    # 잡힌 가점 신호명
    forced: bool = False                                 # 코드가 '위험'을 강제하는가
    forced_by: list[str] = field(default_factory=list)   # 강제 근거(신호명: 매치문구)
    mitigated: list[str] = field(default_factory=list)   # 완화된 위험 신호명
    hits: dict[str, list[str]] = field(default_factory=dict)  # 신호명 -> 매치 문구(디버깅)

    @property
    def all_names(self) -> list[str]:
        """노션 Signals multi_select에 넣을 전체 신호명(위험 + 가점)."""
        return self.risk + self.positive


def analyze(text: str) -> SignalResult:
    """공고 텍스트 → 신호. 순수 함수(테스트 대상)."""
    t = normalize(text)
    res = SignalResult()

    # 1) 가점 먼저 — 완화 규칙이 가점 존재 여부에 의존한다.
    for name, pats in rules.POSITIVE_C.items():
        hits = [m.group(0) for p in pats if (m := p.search(t))]
        if hits:
            res.positive.append(name)
            res.hits[name] = hits

    # 2) 위험. hard 매치는 무조건 강제, soft 매치는 완화 신호가 있으면 강제하지 않음.
    for name, grades in rules.RISK_C.items():
        hard = [m.group(0) for p in grades.get("hard", []) if (m := p.search(t))]
        soft = [m.group(0) for p in grades.get("soft", []) if (m := p.search(t))]
        if not (hard or soft):
            continue
        res.risk.append(name)
        res.hits[name] = hard + soft

        if hard:
            res.forced = True
            res.forced_by.append(f"{name}: {', '.join(hard)}")
            continue
        mitigator = rules.MITIGATED_BY.get(name)
        if mitigator and mitigator in res.positive:
            # 운영+구축 겸업일 수 있다 → 강제하지 않고 LLM 판단에 맡긴다.
            res.mitigated.append(name)
        else:
            res.forced = True
            res.forced_by.append(f"{name}: {', '.join(soft)}")
    return res


def analyze_job(job: JobPosting) -> SignalResult:
    return analyze(f"{job.title} {job.raw_text}")


def analyze_all(jobs: list[JobPosting]) -> dict[str, SignalResult]:
    """source_key -> SignalResult."""
    return {j.source_key: analyze_job(j) for j in jobs}
