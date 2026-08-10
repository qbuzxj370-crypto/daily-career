"""소스 파서 테스트.

⚠️ 여기 픽스처는 **문서 기준 추정 형태**다 — 실제 응답이 아니다. 그러므로 이 테스트는
"매핑이 맞다"를 증명하지 않고, 파서가 (a) 중첩 JSON / XML 형태를 처리하고 (b) id·title이
없으면 건을 버리고 (c) 날짜를 정규화하는지만 증명한다.
**P1(`--probe`)로 실제 응답을 받으면 이 픽스처를 그 덤프로 교체할 것.**
"""
from __future__ import annotations
import json

import pytest

from src.sources.base import SourceError, norm_date
from src.sources.saramin import SaraminSource
from src.sources.worknet import WorknetSource

SARAMIN_BODY = json.dumps({"jobs": {"count": 1, "total": 1, "job": [{
    "id": "12345678",
    "url": "https://www.saramin.co.kr/job/12345678",
    "company": {"detail": {"name": "메가존클라우드", "href": "https://..."}},
    "position": {
        "title": "AWS 클라우드 운영 엔지니어",
        "location": {"name": "서울 > 강남구"},
        "job-type": {"name": "정규직"},
        "industry": {"name": "솔루션·SI·ERP·CRM"},
        "job-code": {"name": "시스템엔지니어,클라우드"},
        "experience-level": {"name": "신입"},
        "required-education-level": {"name": "대졸(2~3년)이상"},
    },
    "keyword": "AWS,인프라구축,주5일",
    "expiration-date": "2026-09-30 23:59:59",
}]}}, ensure_ascii=False)

WORKNET_BODY = """<?xml version="1.0" encoding="UTF-8"?>
<wantedRoot>
  <total>1</total>
  <wanted>
    <company>OO대학교</company>
    <title>전산실 정보시스템 운영 담당</title>
    <wantedAuthNo>K20260101000001</wantedAuthNo>
    <region>대전광역시 유성구</region>
    <career>신입</career>
    <empTpNm>기간의 정함이 있는 근로계약</empTpNm>
    <closeDt>20260910</closeDt>
    <minEdubg>대졸이상</minEdubg>
    <holidayTpNm>주5일근무</holidayTpNm>
    <wantedInfoUrl>https://www.work.go.kr/detail/K20260101000001</wantedInfoUrl>
  </wanted>
</wantedRoot>"""


def test_saramin_parse_nested_fields():
    jobs = SaraminSource.parse(SARAMIN_BODY, "cloud")
    assert len(jobs) == 1
    j = jobs[0]
    assert j.source_key == "saramin:12345678"
    assert j.company == "메가존클라우드" and j.title == "AWS 클라우드 운영 엔지니어"
    assert j.deadline == "2026-09-30"
    assert j.location == "서울 > 강남구" and j.experience == "신입"
    assert j.role == "cloud" and j.url.endswith("12345678")
    # raw_text는 판정에 쓸 합본 — 업종·직종·키워드가 들어가야 신호 규칙이 걸린다.
    assert "인프라구축" in j.raw_text and "솔루션" in j.raw_text


def test_saramin_drops_items_without_id_or_title():
    body = json.dumps({"jobs": {"job": [{"url": "x"}]}})
    assert SaraminSource.parse(body, "cloud") == []


def test_saramin_api_error_is_raised():
    with pytest.raises(SourceError):
        SaraminSource.parse('{"error": "invalid access-key"}', "cloud")


def test_saramin_bad_json_is_raised():
    with pytest.raises(SourceError):
        SaraminSource.parse("<html>rate limited</html>", "cloud")


def test_worknet_parse_xml():
    jobs = WorknetSource.parse(WORKNET_BODY, "public_it")
    assert len(jobs) == 1
    j = jobs[0]
    assert j.source_key == "worknet:K20260101000001"
    assert j.company == "OO대학교" and j.deadline == "2026-09-10"
    assert "주5일근무" in j.raw_text


def test_worknet_parse_json_variant():
    """returnType=JSON으로 오는 경우도 같은 결과여야 한다(P1에서 어느 쪽인지 확정)."""
    body = json.dumps({"wantedRoot": {"wanted": [{
        "company": "OO대학교", "title": "전산실 운영", "wantedAuthNo": "K1",
        "closeDt": "2026-09-10", "wantedInfoUrl": "https://x",
    }]}}, ensure_ascii=False)
    jobs = WorknetSource.parse(body, "public_it")
    assert len(jobs) == 1 and jobs[0].source_key == "worknet:K1"


def test_worknet_api_error_envelope():
    body = ("<OpenAPI_ServiceResponse><cmmMsgHeader>"
            "<returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg>"
            "</cmmMsgHeader></OpenAPI_ServiceResponse>")
    with pytest.raises(SourceError, match="SERVICE_KEY"):
        WorknetSource.parse(body, "public_it")


@pytest.mark.parametrize("raw,want", [
    ("2026-09-30 23:59:59", "2026-09-30"),
    ("20260910", "2026-09-10"),
    ("2026.09.10", "2026-09-10"),
    ("2026/9/1", "2026-09-01"),
    ("상시채용", None),
    ("", None),
    (None, None),
    ("2026-13-45", None),
])
def test_norm_date(raw, want):
    assert norm_date(raw) == want
