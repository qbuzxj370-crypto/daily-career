"""소스 파서 테스트.

**워크넷 픽스처는 실제 응답이다** — 2026-08-10 `srcKeyword=클라우드` 검색 결과에서 행 2개를
그대로 잘라 왔다(주석·이미지 등 무관한 마크업만 제거). 따라서 워크넷 테스트는 매핑이 맞다는
증거가 된다.

⚠️ **사람인 픽스처는 여전히 문서 기준 추정 형태다** — 실제 응답이 아니다. 사람인 테스트는
"매핑이 맞다"를 증명하지 않고, 파서가 중첩 JSON을 처리하고 id·title이 없으면 건을 버리는지만
증명한다. 승인 후 `--probe saramin`으로 받은 덤프로 교체할 것.
"""
from __future__ import annotations
import json
from datetime import date

import pytest

from config import settings
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

# 2026-08-10 work24 실제 검색 결과에서 잘라온 행 2개 + 총건수 hidden.
WORKNET_BODY = """<input type="hidden" name="totalRecordCount" value="613" />
<table><tbody>
<tr id="list1"> <td class="al_left pd24"> <div class="cell"> <div class="box_chk-group"> <label> <span>
<input class="vtalm3" type="checkbox" id="chkboxWantedAuthNo0"
 value="K120612608100071|VALIDATION|(주)에스티이지|(주)에스티이지 26&#039;하반기 솔루션 본부 신입 및 경력 정규직 채용"
 title="(주)에스티이지 선택 후 채용정보 비교검색"/>
<a href="#none" class="cp_name" onclick="fnOpenPopup('2148823105');"> (주)에스티이지 </a>
</span> </label> </div> </div> <div class="cell">
<a href="/wk/a/b/1500/empDetailAuthView.do?wantedAuthNo=K120612608100071&infoTypeCd=VALIDATION&infoTypeGroup=tb_workinfoworknet"
 class="t3_sb" target="_new"> (주)에스티이지 26'하반기 솔루션 본부 신입 및 경력 정규직 채용 </a>
</div> </td> <td class="link pd24"> <ul class="emp_info_dtl">
<li class="dollar"> <p> <span class="item b1_sb"> 연봉 3,500 만원 이상 </span> </p> </li>
<li class="member"> <p> <span class="item sm"> 경력무관 </span> <span class="item sm"> 학력무관 </span> </p> </li>
<li class="time"> <p> <span class="item sm 2"> 주5일 </span> <span class="item sm 3">주 40시간 근로</span> </p> </li>
<li class="site"> <p> 서울특별시 금천구 가산디지털1로 </p> </li>
</ul> </td> <td class="pd24"> <strong id="dDayInfo0"></strong>
<script>var date = '2026-08-28';</script>
<p class="s1_r">마감일 : 2026-08-28</p> <p class="s1_r">등록일 : 2026-08-10</p> </td> </tr>
<tr id="list2"> <td class="al_left pd24"> <div class="cell"> <div class="box_chk-group"> <label> <span>
<input class="vtalm3" type="checkbox" id="chkboxWantedAuthNo1"
 value="K160092608100003|VALIDATION|주식회사 터빈크루|AI/IoT/웹 서비스 개발을 위한 능숙한 네트워크 시스템 개발자를 모집합니다."/>
</span> </label> </div> </div> <div class="cell">
<a href="/wk/a/b/1500/empDetailAuthView.do?wantedAuthNo=K160092608100003&infoTypeCd=VALIDATION&infoTypeGroup=tb_workinfoworknet"
 class="t3_sb" target="_new"> AI/IoT/웹 서비스 개발을 위한 능숙한 네트워크 시스템 개발자를... </a>
</div> </td> <td class="link pd24"> <ul class="emp_info_dtl">
<li class="dollar"> <p> <span class="item b1_sb"> 월급 240 만원 이상 </span> </p> </li>
<li class="member"> <p> <span class="item sm"> 경력1년 </span> <span class="item sm"> 학력무관 </span> </p> </li>
<li class="time"> <p> <span class="item sm 2"> 주5일 </span> <span class="item sm 3">주 40시간 근로</span>
 <span class="item sm 4"> 08:00 ~ 17:00 </span> </p> </li>
<li class="site"> <p> 전남광주통합특별시 나주시 빛가람로 </p> </li>
</ul> </td> <td class="pd24"> <strong id="dDayInfo1"></strong>
<script>var date = '2026-10-08';</script>
<p class="s1_r">마감일 : 2026-10-08</p> <p class="s1_r">등록일 : 2026-08-10</p> </td> </tr>
</tbody></table>"""


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


def test_worknet_parse_real_rows():
    """실제 응답 기준 필드 매핑. 체크박스 value가 1차 앵커다."""
    jobs = WorknetSource.parse(WORKNET_BODY, "cloud")
    assert len(jobs) == 2
    j = jobs[0]
    assert j.source_key == "worknet:K120612608100071"
    assert j.company == "(주)에스티이지"
    # &#039; 엔티티가 아포스트로피로 복원돼야 한다.
    assert "26'하반기" in j.title
    assert j.url == ("https://www.work24.go.kr/wk/a/b/1500/empDetailAuthView.do"
                     "?wantedAuthNo=K120612608100071&infoTypeCd=VALIDATION"
                     "&infoTypeGroup=tb_workinfoworknet")
    assert j.deadline == "2026-08-28"
    assert j.location == "서울특별시 금천구 가산디지털1로"
    assert j.experience == "경력무관 학력무관"


def test_worknet_work_time_reaches_raw_text():
    """근무형태(주5일·근무시간)는 '주간 중심' 가점 판정의 근거이므로 판정 텍스트에 실려야 한다."""
    jobs = WorknetSource.parse(WORKNET_BODY, "cloud")
    assert "주5일" in jobs[0].raw_text
    assert "08:00 ~ 17:00" in jobs[1].raw_text


def test_worknet_total_count():
    assert WorknetSource.total_count(WORKNET_BODY) == 613


def test_worknet_total_count_reads_pagination_script():
    """실제 검색 페이지는 hidden input이 아니라 페이징 스크립트에 총건수를 싣는다."""
    body = "var paginationInfo = { currentPageNo : 1, totalRecordCount : 614, };"
    assert WorknetSource.total_count(body) == 614


# --- 검색 필터 (2026-08-10 실측으로 확정한 파라미터 이름·형식) -----------------

def test_worknet_reg_window_is_yyyymmdd_without_hyphens():
    """등록일에 하이픈을 넣으면 work24가 결과를 0건으로 돌려준다 — 형식이 곧 기능이다."""
    src = WorknetSource(today=date(2026, 8, 14))         # 금요일
    start, end = src.reg_window()
    assert (start, end) == ("20260808", "20260814")      # 오늘-6일 ~ 오늘
    assert "-" not in start and "-" not in end


def test_worknet_params_carry_all_three_filters():
    src = WorknetSource(today=date(2026, 8, 14))
    p = src._params("클라우드", count=30)
    assert p["searchMode"] == "Y"                        # 이게 빠지면 키워드가 무시된다
    assert p["careerTypes"] == settings.WORKNET_CAREER_TYPES
    assert p["academicGbn"] == settings.WORKNET_ACADEMIC_GBN
    assert (p["regDateStdt"], p["regDateEndt"]) == ("20260808", "20260814")
    # 체크박스 name(`careerType`)으로 보내면 서버가 무시한다 — 복수형만 유효하다.
    assert "careerType" not in p


def test_worknet_empty_filter_setting_is_omitted_not_blank(monkeypatch):
    """빈 문자열을 보내면 '필터 없음'이 되므로 파라미터 자체를 빼야 한다."""
    monkeypatch.setattr(settings, "WORKNET_CAREER_TYPES", "")
    monkeypatch.setattr(settings, "WORKNET_ACADEMIC_GBN", "")
    p = WorknetSource(today=date(2026, 8, 14))._params("클라우드", count=30)
    assert "careerTypes" not in p and "academicGbn" not in p


def test_worknet_empty_result_is_not_an_error():
    """총건수 0 = 조건에 맞는 공고가 없는 주. 필터를 걸면 실제로 자주 일어난다."""
    body = ('var paginationInfo = { totalRecordCount : 0, };'
            '<tbody><tr><td>검색어 <strong>MSP</strong>에 대한 검색 결과가 없습니다.</td></tr></tbody>'
            # 0건 페이지에도 이 id를 참조하는 JS가 그대로 실려 온다 — 오류 판별에 쓰면 안 된다.
            '<script>$tr.find("[id^=chkboxWantedAuthNo]").val()</script>')
    assert WorknetSource.parse(body, "cloud") == []


def test_worknet_layout_change_raises():
    """총건수는 있는데 행을 못 뽑았다 = 레이아웃 변경. 조용히 0건으로 넘기면 안 된다."""
    body = ('var paginationInfo = { totalRecordCount : 42, };'
            '<div><input id="chkboxWantedAuthNo0" value="K1|V|회사|제목"/></div>')
    with pytest.raises(SourceError, match="구조가 바뀐"):
        WorknetSource.parse(body, "cloud")


def test_worknet_missing_total_raises():
    """총건수조차 없으면 검색 결과 페이지가 아니다(점검 안내·오류 페이지 등)."""
    with pytest.raises(SourceError, match="총건수를 찾지 못했"):
        WorknetSource.parse("<html><body>시스템 점검 중입니다</body></html>", "cloud")


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
