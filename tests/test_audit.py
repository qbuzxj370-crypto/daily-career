# -*- coding: utf-8 -*-
"""직군 모집단 감사 — 신호를 **재기만** 하고 거르지 않는지 지킨다.

이 모듈이 조용히 '판정'으로 변하면 모집단이 설명 없이 줄어든다. 그건 통계층에서
치명적이다 — 어느 공고가 왜 빠졌는지 모른 채 모든 비율이 바뀐다.
"""
from __future__ import annotations

from types import SimpleNamespace

from src import audit
from src.sources.worknet_detail import DetailInfo

# 실측: K161322610010017 (호원대 학사교직지원팀). 검색어 `전산직`이 물어온 행정직이다.
HOWON = DetailInfo(
    source_id="K161322610010017",
    duty="대학혁신지원사업 프로그램 관련 업무 - 재학생 유지관리 등 기타 학사 관련 업무",
    preferred="- 전산활용 가능자 우대",
    computer_skill="문서작성 (워드프로세스 활용),표계산 (스프레드시트 활용)",
    worker_count=300,
)
HOWON_POST = SimpleNamespace(source_id="K161322610010017", company="호원대학교",
                             title="호원대학교 지원직 직원 채용 공고")


def test_contaminating_posting_yields_zero_requirements():
    """오염의 1차 신호. 행정직은 스킬 사전에 걸리는 게 없다."""
    row = audit.row_for(HOWON_POST, HOWON, keyword="전산직")
    assert row.req_count == 0
    assert row.skills == ()


def test_office_only_flags_the_goyong24_boilerplate():
    """`컴퓨터 활용 능력`이 사무 도구뿐이면 표시한다 — 판정이 아니라 표시다."""
    assert audit.row_for(HOWON_POST, HOWON, keyword="전산직").office_only is True


def test_office_only_is_false_when_field_is_empty():
    """**없는 것에서 추론하지 않는다.** 빈 칸은 '사무직'이 아니라 '모른다'다."""
    info = DetailInfo(source_id="X", computer_skill="")
    assert audit.row_for(SimpleNamespace(source_id="X", title="", company=""),
                         info, keyword="k").office_only is False


def test_detail_failure_is_not_zero_requirements():
    """상세 수집 실패를 '요건 없음'으로 세면 오염률이 거짓으로 올라간다."""
    row = audit.row_for(HOWON_POST, None, keyword="전산직")
    assert row.detail_ok is False
    a = audit.Audit(role="public_it", rows=[row])
    assert a.detail_failed == 1
    assert a.no_requirements == 0, "실패를 오염으로 세지 말 것"


def test_by_keyword_attributes_contamination_to_its_source():
    """어느 검색어가 오염원인지 가려야 검색어를 고칠 수 있다."""
    good = DetailInfo(source_id="K999", duty="Java, Spring Boot 기반 API 개발")
    a = audit.Audit(role="public_it", rows=[
        audit.row_for(HOWON_POST, HOWON, keyword="전산직"),
        audit.row_for(SimpleNamespace(source_id="K999", title="백엔드 개발자", company="예시"),
                      good, keyword="백엔드"),
    ])
    assert a.by_keyword()["전산직"] == (1, 1)
    assert a.by_keyword()["백엔드"] == (1, 0)


def test_audit_does_not_drop_rows():
    """거르지 않는다 — 들어온 수와 표의 수가 같아야 한다."""
    rows = [audit.row_for(HOWON_POST, HOWON, keyword="전산직"),
            audit.row_for(HOWON_POST, None, keyword="전산실")]
    a = audit.Audit(role="public_it", rows=rows)
    assert a.total == 2
    assert audit.report(a).count("호원대학교") >= 2


def test_report_has_no_markdown_table():
    """`md_to_notion.py`가 표를 파싱하지 못한다 — 프로젝트 전역 제약."""
    a = audit.Audit(role="public_it",
                    rows=[audit.row_for(HOWON_POST, HOWON, keyword="전산직")])
    assert "|---" not in audit.report(a)
