"""신호 규칙 테스트 — 이 프로젝트에서 **실제 테스트를 두는 유일한 부분**.

이유: 규칙은 문자열에 대한 순수 함수이고, 사용자의 판정 기준을 그대로 인코딩하며,
패턴 하나가 조용히 깨지면 위험 공고가 적합으로 넘어간다(도구의 존재 이유가 무너진다).

  python -m pytest tests -q
"""
from __future__ import annotations
import pytest

from src.evaluator import Verdict, apply_rules, rule_verdict
from src.signals import analyze
from src.sources.base import JobPosting


# ---------------------------------------------------------------- 위험: 관제 전담

@pytest.mark.parametrize("text", [
    "IDC 관제센터 3교대 근무",
    "4조 3교대 상주 근무",
    "24*365 무중단 모니터링",
    "24/365 관제 운영",
    "24 365 관제",
    "교대근무 (주간/야간)",
])
def test_shift_patterns_force_risk(text):
    """근무형태 신호(hard)는 완화 없이 무조건 위험 강제."""
    r = analyze(text)
    assert "관제 전담" in r.risk
    assert r.forced is True


@pytest.mark.parametrize("text", ["NOC 운영 담당", "상주 관제 업무", "모니터링 요원 모집"])
def test_soft_control_patterns_force_when_alone(text):
    """관제 성격 신호(soft)는 완화 신호가 없으면 위험 강제."""
    r = analyze(text)
    assert "관제 전담" in r.risk
    assert r.forced is True


def test_control_mitigated_by_build_org():
    """완화 규칙: `관제` + `구축 조직 동거` → 강제하지 않고 LLM 판단에 맡긴다."""
    r = analyze("고객사 인프라 구축 및 관제 업무 병행, 신규 도입 프로젝트 참여")
    assert "관제 전담" in r.risk
    assert "구축 조직 동거" in r.positive
    assert r.forced is False
    assert "관제 전담" in r.mitigated


def test_shift_not_mitigated_by_build_org():
    """근무형태 신호는 구축 신호가 있어도 완화되지 않는다."""
    r = analyze("인프라 구축 및 운영, 3교대 근무")
    assert r.forced is True
    assert "관제 전담" not in r.mitigated


# ---------------------------------------------------------------- 위험: 소기업·1인

@pytest.mark.parametrize("text", [
    "1인 전산 담당자 모집",
    "전산 담당 1명 채용",
    "대표 직속으로 사내 IT 전반 관리",
    "직원 5명 규모의 사무실",
])
def test_small_company_signals(text):
    r = analyze(text)
    assert "소기업·1인" in r.risk
    assert r.forced is True


# ---------------------------------------------------------------- 가점

def test_positive_signals_two_hits():
    """docs/career-plan.md §검증 P2: `구축` + `주 5일` 공고가 가점 2개를 받는다."""
    r = analyze("클라우드 인프라 구축 및 운영, 주 5일 상시 주간 근무")
    assert set(r.positive) == {"구축 조직 동거", "주간 중심"}
    assert r.risk == []
    assert r.forced is False


@pytest.mark.parametrize("text", [
    "09:00~18:00 근무", "0900 1800 근무", "교대 없는 주간 근무", "상시 주간 고정",
])
def test_daytime_patterns(text):
    assert "주간 중심" in analyze(text).positive


@pytest.mark.parametrize("text", [
    "SI 프로젝트 참여", "설계 및 구축 담당", "온프렘 이관 프로젝트", "클라우드 마이그레이션", "PoC 지원",
])
def test_build_org_patterns(text):
    assert "구축 조직 동거" in analyze(text).positive


def test_neutral_text_has_no_signals():
    r = analyze("스위치·라우터 구성 관리 및 회선 장애 대응")
    assert r.risk == [] and r.positive == [] and r.forced is False


def test_case_insensitive_and_whitespace():
    assert "구축 조직 동거" in analyze("si 프로젝트").positive
    assert analyze("3 교대  근무").forced is True


# ---------------------------------------------------------------- 규칙이 LLM을 이긴다

def _job() -> JobPosting:
    return JobPosting(source="mock", source_id="1", title="t", company="c",
                      url="", role="cloud", raw_text="")


def test_rules_override_llm_verdict():
    """LLM이 '적합'을 줘도 규칙이 확정한 위험은 코드가 되돌린다."""
    sig = analyze("24*365 관제 3교대")
    v = Verdict(source_key="mock:1", verdict="적합", score=5, summary="좋아 보임", reason="LLM 근거")
    out = apply_rules(v, sig)
    assert out.verdict == "위험"
    assert out.score <= 2          # verdict와 점수가 어긋나 정렬이 뒤집히지 않도록
    assert out.by_rule is True
    assert "관제 전담" in out.risk_signals
    assert "규칙 강제" in out.reason


def test_rules_do_not_override_when_mitigated():
    sig = analyze("인프라 구축 및 관제 병행")
    v = Verdict(source_key="mock:1", verdict="보통", score=3, reason="LLM 근거")
    out = apply_rules(v, sig)
    assert out.verdict == "보통"    # 완화 → LLM 판단 유지
    assert "관제 전담" in out.risk_signals


def test_rule_only_verdict_matrix():
    assert rule_verdict(_job(), analyze("3교대 관제")).verdict == "위험"
    assert rule_verdict(_job(), analyze("인프라 구축, 주 5일")).verdict == "적합"
    assert rule_verdict(_job(), analyze("일반 사무 지원")).verdict == "보통"
    # 사원수는 API가 주지 않는다 → 규칙 판정은 회사 규모를 '판단 불가'로 남긴다(추측 금지)
    assert rule_verdict(_job(), analyze("일반 사무 지원")).company_size == "판단 불가"


def test_signal_names_are_canonical():
    """노션 Signals multi_select 옵션과 규칙 신호명이 어긋나지 않는지."""
    from config import rules
    from src import notion_pub
    schema = notion_pub._schema_properties()["Signals"]["multi_select"]["options"]
    assert {o["name"] for o in schema} == set(rules.ALL_SIGNAL_NAMES)
