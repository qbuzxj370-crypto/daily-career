"""워크넷 상세 페이지 파서 테스트.

⚠️ **픽스처의 지위를 정확히 알 것** (`CLAUDE.md` §P1과 같은 구분).
  - **라벨과 값은 실제 관측치다.** 2026-09-30에 실제 공고 2건(K170082609300018,
    K130042609300051)의 렌더된 화면에서 읽은 문자열을 그대로 썼다.
  - **HTML 구조는 추정이다.** 원본 HTML을 받지 못했으므로(이그레스 정책) 표 구조는 내가
    지어낸 것이다. 그래서 이 테스트가 초록이어도 **실제 페이지에서 동작한다는 증거가 아니다** —
    `--probe-detail`로 덤프를 받아 라벨이 실제로 저 문자열인지 확인해야 P1이 끝난다.

다만 파서가 **마크업에 앵커를 걸지 않기 때문에** 구조 추정이 틀려도 라벨이 맞으면 동작한다.
그게 이 설계를 고른 이유이고, 아래 `test_duty_survives_label_word_inside_body`가 그 설계가
실제로 필요한 이유(짧은 라벨의 본문 오탐)를 고정한다.
"""
from __future__ import annotations

import pytest

from src.sources.base import JobPosting, SourceError, SourceUnreachable
from src.sources.worknet_detail import (
    DetailInfo, WorknetDetailSource, detail_url, extract, parse, split_query,
    to_lines,
)

# 관측된 본문 원문(K130042609300051). **`경력직`이 들어 있는 것이 중요하다** —
# `경력` 라벨이 이 단어에 오탐하면 본문이 첫 문장에서 잘린다.
DUTY_TEXT = (
    "양정역1번출구 인근에 위치한 회계사무실입니다 세무회계사무실 1년이상 경력직을 "
    "모집하고 있습니다 신입 또는 경력직 1년차 (1명) 1. 주요 업무범위 - 원천세 신고, "
    "부가가치세 신고, 소득세 신고, 법인세 신고, 4대보험 업무"
)

# 구조는 추정, **라벨·값은 실제 덤프에서 확정**(2026-10-01, K161322610010018).
# ⚠️ 이전 판은 라벨을 `우대사항`·`근무지역`으로 적었는데 둘 다 틀렸다 —
#    `우대사항`은 구역 제목이고, 실제 칸은 `기타 우대사항`·`지역`이다.
DETAIL_HTML = f"""
<html><head>
<script>var dday = 7; var 근로자수 = '속임수';</script>
<style>.tit {{ color: red }}</style>
</head><body>
<div class="summary">
  <table><tbody>
    <tr><th>경력</th><td>관계없음</td><th>학력</th><td>학력무관</td></tr>
    <tr><th>임금</th><td>면접 후 결정</td></tr>
    <tr><th>지역</th><td>부산 부산진구</td></tr>
  </tbody></table>
</div>
<h3>모집요강</h3>
<table><tbody>
  <tr><th>직무내용</th><td>{DUTY_TEXT}</td></tr>
  <tr><th>기타 우대사항</th><td>전산회계1급, 세무회계(2급)</td></tr>
  <tr><th>자격면허</th><td>우대 : 전산회계1급, 세무회계(2급)</td></tr>
  <tr><th>전공</th><td>-</td></tr>
  <tr><th>컴퓨터 활용 능력</th><td>문서작성(워드프로세스 활용), 회계프로그램</td></tr>
</tbody></table>
<h3>근무조건</h3>
<table><tbody>
  <tr><th>근무 형태</th><td>주 5일 근무</td></tr>
  <tr><th>주 소정근로시간</th><td>40시간</td></tr>
</tbody></table>
<h3>기업정보</h3>
<table><tbody>
  <tr><th>근로자수</th><td>2명</td></tr>
</tbody></table>
<div class="footer">개인정보처리방침 이용약관 근로자수 안내</div>
</body></html>
"""


@pytest.fixture
def info() -> DetailInfo:
    return parse(DETAIL_HTML, source_id="K130042609300051")


# ---------------------------------------------------------------- 핵심 수확

def test_duty_body_is_extracted(info: DetailInfo):
    """★본문이 나온다 — 목록 페이지에 없던 것이고, 요건 통계의 원재료다."""
    assert info.duty.startswith("양정역1번출구")
    assert "원천세 신고" in info.duty


def test_duty_survives_label_word_inside_body(info: DetailInfo):
    """★본문 안의 `경력직`이 `경력` 라벨로 오탐되어 본문을 자르지 않는다.

    줄 단위로 끊지 않고 텍스트 전체에서 라벨 위치만 찾는 구현은 여기서 깨진다.
    본문 마지막 문장까지 들어와야 통과한다.
    """
    assert "경력직" in info.duty
    assert "4대보험 업무" in info.duty, "본문이 라벨 오탐으로 잘렸다"


def test_worker_count_is_a_number(info: DetailInfo):
    """★근로자수가 숫자로 나온다 — `소기업·1인` 기준을 문구 추측 없이 확정할 수 있다."""
    assert info.worker_count == 2
    assert info.fields["근로자수"] == "2명"


def test_preferred_is_separate_from_duty(info: DetailInfo):
    """필수/우대 분리가 구조적으로 가능하다 — 통계의 1급 축이 될 수 있다."""
    assert info.preferred == "전산회계1급, 세무회계(2급)"
    assert "전산회계1급" not in info.duty


# ---------------------------------------------------------------- 추출 규칙

def test_label_whitespace_variants_match():
    """`자격 면허`(정본)와 `자격면허`(픽스처)가 같은 라벨로 잡힌다."""
    info = parse(DETAIL_HTML)
    assert info.license.startswith("우대")


def test_dash_becomes_empty(info: DetailInfo):
    """화면의 `-`는 값이 아니라 빈 칸이다. 그대로 저장하면 통계에 쓰레기가 쌓인다."""
    assert info.major == ""


def test_script_and_style_are_dropped():
    """script 안의 `근로자수` 변수가 값으로 새어 들어오지 않는다."""
    lines = to_lines(DETAIL_HTML)
    assert not any("속임수" in line for line in lines)


def test_first_occurrence_wins():
    """같은 라벨이 뒤에 다시 나와도(푸터) 위쪽 값을 유지한다."""
    info = parse(DETAIL_HTML)
    assert info.worker_count == 2   # 푸터의 '근로자수 안내'가 덮어쓰지 않았다


def test_inline_label_and_value_on_one_line():
    """`근로자수 : 5명`처럼 한 줄에 붙어 와도 값을 뽑는다."""
    html = "<p>근로자수 : 5명</p><p>직무내용</p><p>서버 운영</p>"
    info = parse(html)
    assert info.worker_count == 5
    assert info.duty == "서버 운영"


def test_boundary_label_stops_value():
    """값으로 뽑지 않는 라벨(`기업정보`)도 구간의 끝으로는 작동한다."""
    html = ("<p>직무내용</p><p>서버 운영 업무</p>"
            "<p>기타 우대사항</p><p>AWS 경험</p>"
            "<p>기업정보</p><p>주식회사 테스트</p>")
    info = parse(html)
    assert info.preferred == "AWS 경험"
    assert "주식회사" not in info.preferred
    assert "우대" not in info.duty, "본문이 다음 라벨을 삼켰다"


# ---------------------------------------------------------------- 실패 규약

def test_empty_body_raises():
    with pytest.raises(SourceError):
        parse("")


def test_page_without_labels_raises():
    """로그인 유도·오류 페이지를 조용히 빈 값으로 넘기지 않는다.

    조용히 넘기면 매주 본문이 비어도 이유를 알 수 없다 — 목록 파서가 결과 0건과 구조
    변경을 구분하는 것과 같은 원칙이다(`docs/ref.md` §4-14).
    """
    with pytest.raises(SourceError, match="라벨을 찾지 못했습니다"):
        parse("<html><body><h1>로그인이 필요합니다</h1></body></html>")


def test_single_label_is_not_enough():
    """라벨 1개는 우연히 걸릴 수 있으므로 상세 페이지로 인정하지 않는다."""
    with pytest.raises(SourceError):
        parse("<html><body><p>전공</p><p>컴퓨터공학</p></body></html>")


# ---------------------------------------------------------------- 규모 밴드

@pytest.mark.parametrize("count,expected", [
    (1, "소기업·1인"),
    (2, "소기업"),
    (9, "소기업"),
    (10, "중소기업"),
    (99, "중소기업"),
    (100, "중견기업"),
    (1000, "대기업"),
])
def test_size_band_boundaries(count, expected):
    """1명과 2~9명을 가른다 — 기준의 문제는 '작은 회사'가 아니라 '혼자인 자리'다."""
    assert DetailInfo(source_id="x", worker_count=count).size_band() == expected


def test_size_band_is_none_without_count():
    """숫자가 없으면 추측하지 않는다. `판단 불가`를 지어내지 않는 것과 같은 원칙이다."""
    assert DetailInfo(source_id="x").size_band() is None


def test_size_band_is_not_wired_into_rules():
    """규모 밴드는 판정에 자동 반영되지 않는다.

    `소기업·1인`은 사용자의 4대 판정 기준 중 하나고, CLAUDE.md가 "패턴의 등급을 바꾸는 것
    = 사용자의 판정 기준을 바꾸는 것"이라고 못박았다. 이 모듈이 `config/rules.py`나
    `evaluator`를 건드리지 않는다는 것을 고정한다.
    """
    import src.sources.worknet_detail as mod
    source = mod.__file__ and open(mod.__file__, encoding="utf-8").read()
    assert "config.rules" not in source
    assert "config import rules" not in source
    assert "evaluator" not in source


# ---------------------------------------------------------------- 병합·URL

def test_to_extra_keeps_raw_text_unprocessed(info: DetailInfo):
    """원문을 가공하지 않고 넣는다 — 사전을 고칠 때 소급 재적용이 가능해야 한다."""
    extra = info.to_extra()
    assert extra["duty"] == info.duty
    assert extra["preferred"] == info.preferred
    assert extra["worker_count"] == 2
    assert extra["size_band"] == "소기업"


def test_missing_lists_empty_fields(info: DetailInfo):
    """probe가 라벨 보정 필요를 알려주는 근거."""
    assert "major" in info.missing()      # 픽스처에서 '-'
    assert "duty" not in info.missing()


def test_detail_url_has_required_params():
    url = detail_url("K170082609300018")
    assert "wantedAuthNo=K170082609300018" in url
    assert "infoTypeCd=VALIDATION" in url
    assert url.startswith("https://www.work24.go.kr/wk/a/b/1500/")


def _posting(source_id: str) -> JobPosting:
    return JobPosting(source="worknet", source_id=source_id, title="t", company="c",
                      url="", role="cloud")


def test_enrich_merges_into_extra(monkeypatch):
    src = WorknetDetailSource()
    monkeypatch.setattr(src, "_raw", lambda url: DETAIL_HTML)
    jobs = [_posting("A"), _posting("B")]
    assert src.enrich(jobs, log=lambda *_: None) == 2
    assert jobs[0].extra["worker_count"] == 2


def test_enrich_survives_individual_failure(monkeypatch):
    """1건 실패가 나머지를 막지 않는다 — 상세는 보강이지 필수 경로가 아니다."""
    src = WorknetDetailSource()
    calls = {"n": 0}

    def flaky(url: str) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise SourceUnreachable("연결 실패")
        return DETAIL_HTML

    monkeypatch.setattr(src, "_raw", flaky)
    jobs = [_posting("A"), _posting("B")]
    assert src.enrich(jobs, log=lambda *_: None) == 1
    assert "worker_count" not in jobs[0].extra
    assert jobs[1].extra["worker_count"] == 2


def test_extract_returns_empty_for_no_lines():
    assert extract([]) == {}


# ============================================================ 쿼리 보존 (회귀)

def test_split_query_separates_base_and_params():
    base, params = split_query(
        "https://www.work24.go.kr/wk/a/b/1500/empDetailAuthView.do"
        "?wantedAuthNo=K1&infoTypeCd=VALIDATION&infoTypeGroup=tb_x")
    assert base == "https://www.work24.go.kr/wk/a/b/1500/empDetailAuthView.do"
    assert params == {"wantedAuthNo": "K1", "infoTypeCd": "VALIDATION",
                      "infoTypeGroup": "tb_x"}


def test_split_query_handles_url_without_query():
    base, params = split_query("https://x.test/a/b")
    assert base == "https://x.test/a/b" and params == {}


def test_raw_does_not_lose_query_parameters(monkeypatch):
    """★`http_get(url, {})`은 URL의 쿼리를 통째로 지운다 — 실제로 났던 사고다.

    httpx는 params가 주어지면 merge가 아니라 **replace**한다. 빈 dict을 넘기면
    `wantedAuthNo`가 사라지고, work24는 200으로 883바이트 스텁
    (`구인정보를 확인할 수 없습니다`)을 돌려준다. 네트워크·차단·파서 어디를 봐도
    원인이 안 보여서 요청 조건 7가지를 전부 실패로 오판했다.

    이 테스트는 `_raw`가 넘기는 (base, params)에 공고번호가 **살아 있는지**를 고정한다.
    """
    seen = {}

    def fake_http_get(url, params, **kw):
        seen["url"] = url
        seen["params"] = params
        return "text/html", DETAIL_HTML

    monkeypatch.setattr("src.sources.worknet_detail.http_get", fake_http_get)
    monkeypatch.setattr("src.sources.worknet_detail._throttle", lambda *a: 0.0)

    src = WorknetDetailSource()
    src.fetch_one("K161322610010018")

    assert "?" not in seen["url"], "쿼리는 params로 넘겨야 한다"
    assert seen["params"]["wantedAuthNo"] == "K161322610010018"
    assert seen["params"]["infoTypeCd"] == "VALIDATION"


def test_http_get_guard_keeps_query_when_params_empty(monkeypatch):
    """`base.http_get`도 같은 지뢰를 막는다 — 다른 호출자가 다시 밟지 않도록."""
    import httpx

    from src.sources import base as base_mod

    captured = {}

    class FakeResp:
        status_code = 200
        headers = {"content-type": "text/html"}
        text = "ok"

    def fake_get(url, params=None, **kw):
        captured["url"] = str(httpx.Request("GET", url, params=params).url)
        return FakeResp()

    monkeypatch.setattr(httpx, "get", fake_get)
    base_mod.http_get("https://x.test/a?k=v", {}, timeout=5)
    assert "k=v" in captured["url"], "빈 params가 쿼리를 지웠다"


# ------------------------------------------------- 실제 덤프에서 확정된 결함 4개
# 출처: K161322610010018 상세 페이지 (2026-10-01). 아래 줄들은 덤프에서 전사한 것이고
# 추측이 아니다. 네 결함 모두 `--probe-detail` 출력에서 드러났다.

NB = "\xa0"   # 상세 페이지 본문은 공백을 전부 nbsp로 쓴다


def test_tab_strip_does_not_claim_key_with_empty_value():
    """상단 탭 띠의 라벨이 빈 값으로 키를 선점하면 실제 표가 영구히 가려진다.

    이게 `우대사항`이 비어 보였던 원인이다 — 라벨이 틀린 게 아니라, 구역 이름을 나열한
    띠가 본문보다 위에 있어서 '처음 등장만 채택'이 띠를 집었다.
    """
    lines = [
        "기타 우대사항", "복리후생", "전형방법",      # ← 탭 띠. 뒤가 전부 경계라 값이 없다
        "기타 우대사항", f"-{NB}전산활용{NB}가능자{NB}우대",
    ]
    assert extract(lines)["preferred"] == "- 전산활용 가능자 우대"


def test_preferred_keeps_every_list_item():
    """`기타 우대사항`은 한 칸에 목록으로 온다. 1줄로 끊으면 우대 요건 2/3이 사라진다."""
    lines = [
        "기타 우대사항",
        f"-{NB}국가보훈대상자{NB}및{NB}장애인은{NB}관련법에{NB}의거{NB}우대",
        f"-{NB}전산활용{NB}가능자{NB}우대",
        f"-{NB}채용분야{NB}근무{NB}경력자{NB}우대",
        "기타사항",
    ]
    got = extract(lines)["preferred"]
    assert "전산활용" in got and "채용분야" in got, "목록 뒷줄이 잘렸다"


def test_work_hours_comes_from_inline_form_not_help_modal():
    """접힌 도움말 모달이 라벨 뒤에 끼어들어 값 대신 `도움말`이 잡혔다.

    모달 구조를 뚫는 대신 괄호 인라인 형태를 쓴다 — 구조 변경에 덜 민감하다.
    """
    lines = [
        "(주 소정근로시간: 40시간)",                     # ← 안전한 경로
        "주 소정근로시간", "도움말", '※ "주소정근로시간" 이란', "닫기", ": 40시간",
    ]
    assert extract(lines)["work_hours"] == "40시간"


def test_location_label_is_jiyeok_not_geunmujiyeok():
    """`근무지역`이라는 문자열은 페이지에 없다. 라벨은 `지역`이다."""
    lines = ["지역", "전북특별자치도   군산시  임피면 호원대3길 64"]
    assert extract(lines)["location"] == "전북특별자치도 군산시 임피면 호원대3길 64"


def test_nav_strip_jiyeokbyeol_is_not_location():
    """`지역`은 짧아서 걱정되지만, 줄 전체가 같아야 매칭되므로 `지역별`은 안 걸린다."""
    lines = ["지역별", "AI 일자리추천", "테마별"]
    assert "location" not in extract(lines)


def test_preferred_cond_is_not_fed_to_skill_matching():
    """`우대조건`은 고용24 고정 선택지(보훈·장애인)다. 요건으로 넣으면 통계에 사람 분류가 섞인다."""
    from src import requirements
    info = DetailInfo(source_id="X", preferred_cond="보훈취업지원대상자")
    assert "preferred_cond" not in requirements.from_detail.__code__.co_names
    assert info.preferred_cond == "보훈취업지원대상자", "저장은 한다 — 매칭만 안 한다"
