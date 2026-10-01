"""스킬 사전 · 프로필 · 요건 추출 · 매칭 테스트.

이 네 모듈은 **순수 함수 + 문자열**이라 `config/rules.py`와 같은 지위다 — 조용히 깨지면
갭 분석이 거짓말을 하고, 거짓말하는 갭 리포트는 없는 것보다 나쁘다. 그래서 실제 테스트를
유지한다(원본 `CLAUDE.md` §Tests의 `rules.py` 규칙과 같은 논리).

**각 테스트는 왜 그 동작이어야 하는지를 적는다.** 값만 고정하면 다음 세션이 이유를 모르고
뒤집는다.
"""
from __future__ import annotations

import pytest

from config import profile, skills
from src import matcher, requirements as reqs


# ============================================================ 스킬 사전

def test_korean_and_english_aliases_normalize_together():
    """`자바`와 `Java`가 한 정본으로 모인다 — 이게 안 되면 통계가 흩어진다."""
    assert skills.find_skills("자바 개발") == ["Java"]
    assert skills.find_skills("Java 개발") == ["Java"]


def test_longer_alias_wins():
    """`스프링부트`가 `Spring`으로 흡수되지 않는다.

    별칭을 길이 내림차순으로 검사하지 않으면 부트가 사라진다.
    """
    found = skills.find_skills("스프링부트 경험자")
    assert "Spring Boot" in found


def test_short_ascii_alias_requires_word_boundary():
    """`C`가 아무 영문 단어에나 걸리지 않는다.

    경계를 안 걸면 `CI/CD`·`Cloud`·`Container`가 전부 C언어 요구로 잡혀 갭 목록이
    쓰레기가 된다.
    """
    assert "C" not in skills.find_skills("CI/CD 파이프라인 구축")
    assert "C" not in skills.find_skills("Cloud 환경")
    assert "C" in skills.find_skills("C/C++ 개발")


def test_short_korean_alias_does_not_false_match():
    """★`코드리뷰`의 '뷰'가 Vue로 잡히지 않는다 — 실제로 났던 오탐이다.

    2026-09-30 시연에서 백엔드 공고의 "코드리뷰 문화가 있습니다"가 **Vue 요구**로 잡혀
    갭 목록에 프론트 프레임워크가 끼었다. 한글 1~2자 별칭은 단어 경계를 걸 수 없어
    부분 일치가 난다 — 짧은 한글 별칭을 추가하려는 유혹을 이 테스트가 막는다.
    """
    found = skills.find_skills("Git 으로 형상관리하며 코드리뷰 문화가 있습니다")
    assert "Vue" not in found
    assert "Git" in found and "Git 협업" in found


def test_ambiguous_short_ascii_aliases_are_absent():
    """`부트`(부트캠프)와 `TS`(기술지원)는 별칭에서 빠져 있어야 한다."""
    assert "Spring Boot" not in skills.find_skills("부트캠프 수료")
    assert "TypeScript" not in skills.find_skills("TS 엔지니어 모집")


def test_sql_is_not_matched_inside_mysql():
    """`MySQL`의 일부인 `SQL`을 따로 잡지 않는다 — 함의 표(IMPLIES)가 그 자리를 맡는다."""
    assert skills.find_skills("MySQL 사용") == ["MySQL"]
    assert "SQL" in skills.implied("MySQL")


def test_implies_is_transitive():
    """`Spring Boot` → `Spring` → `Java`가 모두 나온다."""
    assert set(skills.implied("Spring Boot")) >= {"Spring", "Java"}


def test_certs_are_separate_from_skills():
    """SQLD는 자격증이지 스킬이 아니다. 섞으면 '자격증=실무경험'이 된다."""
    assert skills.find_certs("SQLD 보유자 우대") == ["SQLD"]
    assert "SQL" not in skills.find_skills("SQLD 보유자 우대")


def test_cert_evidence_does_not_grant_level():
    """자격증은 **부분 증빙**일 뿐이다. 레벨을 채우면 갭 분석이 거짓말을 한다."""
    assert "SQL" in skills.certified_skills(["SQLD"])
    assert profile.level("SQL") == profile.LEVEL_NONE


# ============================================================ 프로필 정본

def test_every_profile_skill_is_canonical():
    """프로필의 스킬 키 오타를 잡는다.

    오타는 조용히 '레벨 0'으로 처리되어 **있는 스킬이 갭으로 나온다.** 사람이 눈으로
    찾기 어려운 종류의 버그라 테스트로 막는다.
    """
    unknown = [s for s in profile.SKILL_LEVELS if s not in skills.SKILLS]
    assert not unknown, f"skills.py에 없는 스킬 이름: {unknown}"


def test_every_profile_cert_is_canonical():
    unknown = [c for c in profile.CERTS if c not in skills.CERTS]
    assert not unknown, f"skills.py에 없는 자격증 이름: {unknown}"


def test_levels_are_in_range():
    assert all(profile.LEVEL_NONE <= lv <= profile.LEVEL_WORK
               for lv in profile.SKILL_LEVELS.values())


def test_backend_is_the_first_target():
    """1목표가 백엔드라는 사실을 고정한다. 우선순위는 노션 정렬·슬랙 노출 순서를 정한다."""
    assert profile.TARGET_ROLES[0] == "backend"


# ============================================================ 요건 추출

def test_preferred_field_becomes_preferred_kind():
    """`우대사항` 항목에서 나온 것은 우대다 — 항목이 kind의 1차 근거."""
    items = reqs.from_texts(duty="Java 개발", preferred="Docker 경험")
    kinds = {i.skill: i.kind for i in items}
    assert kinds["Java"] == reqs.KIND_REQUIRED
    assert kinds["Docker"] == reqs.KIND_PREFERRED


def test_preferred_cue_inside_duty_flips_kind():
    """본문에 적힌 `우대` 문구도 존중한다. 담당자가 본문에 다시 적는 경우가 흔하다."""
    items = reqs.from_texts(duty="Java 개발 담당. Docker 사용 경험 우대")
    kinds = {i.skill: i.kind for i in items}
    assert kinds["Java"] == reqs.KIND_REQUIRED
    assert kinds["Docker"] == reqs.KIND_PREFERRED


def test_preferred_cue_does_not_leak_across_clauses():
    """절 하나의 `우대`가 다른 절의 스킬을 오염시키지 않는다.

    문서 전체를 한 덩어리로 보면 맨 끝 `우대` 한 단어가 앞의 모든 요건을 우대로 바꿔
    필수가 사라진다 — 그러면 '막는 것'이 항상 0개가 된다.
    """
    items = reqs.from_texts(duty="Java 개발이 주 업무입니다. Redis 경험 우대")
    kinds = {i.skill: i.kind for i in items}
    assert kinds["Java"] == reqs.KIND_REQUIRED
    assert kinds["Redis"] == reqs.KIND_PREFERRED


@pytest.mark.parametrize("text,expected", [
    ("Docker 이해 수준", reqs.DEPTH_UNDERSTAND),
    ("Docker 사용 경험", reqs.DEPTH_EXPERIENCE),
    ("Docker 운영 경험 필수", reqs.DEPTH_PROFICIENT),
    ("Docker 능숙하게 다루는 분", reqs.DEPTH_PROFICIENT),
])
def test_depth_from_wording(text, expected):
    """같은 단어라도 `이해`와 `운영 경험`은 다른 요구다 — 준비 방향이 달라진다."""
    items = reqs.from_texts(duty=text)
    assert next(i for i in items if i.skill == "Docker").depth == expected


def test_depth_defaults_to_experience():
    """수식어가 없으면 '경험'으로 본다.

    '이해'를 기본으로 두면 갭이 실제보다 작게 나오고, '실무'를 기본으로 두면 모든 공고가
    불가능해 보인다. 공고가 맨몸으로 `Java`만 적었다면 써 본 것을 기대한다는 뜻이다.
    """
    items = reqs.from_texts(duty="Java, MySQL")
    assert next(i for i in items if i.skill == "Java").depth == reqs.DEPTH_EXPERIENCE


def test_required_beats_preferred_on_dedupe():
    """같은 스킬이 양쪽에 있으면 **필수로 남는다.**

    우대로 낮추면 갭이 과소평가된다 — 이 도구의 실패 방향은 '갭을 작게 보는 것'이다.
    """
    items = reqs.from_texts(duty="Java 개발", preferred="Java 심화")
    java = [i for i in items if i.skill == "Java"]
    assert len(java) == 1
    assert java[0].kind == reqs.KIND_REQUIRED


def test_implied_requirement_is_added_with_evidence():
    """`Spring Boot`만 적힌 공고에서도 `Java`가 요건으로 잡히고, 근거가 남는다."""
    items = reqs.from_texts(duty="Spring Boot 기반 API 개발")
    java = next(i for i in items if i.skill == "Java")
    assert "Spring Boot 요구에 포함" in java.evidence


@pytest.mark.parametrize("text,expected", [
    ("신입", 0), ("경력무관", 0), ("관계없음", 0),
    ("경력 1년 이상", 1), ("경력 3년 이상", 3), ("2~3년", 2),
    ("", None), ("경력", None),
])
def test_required_years(text, expected):
    """범위는 **작은 쪽**을 쓴다 — 지원 가능성의 문턱은 하한이다."""
    assert reqs.required_years(text) == expected


# ============================================================ 매칭

def _req(skill: str, depth: int, kind: str = reqs.KIND_REQUIRED) -> reqs.Requirement:
    return reqs.Requirement(skill=skill, kind=kind, depth=depth, field="duty",
                            evidence="테스트")


def test_owned_at_required_depth_is_met():
    """Java는 실습 레벨이므로 '경험' 요구를 충족한다."""
    r = matcher.match([_req("Java", reqs.DEPTH_EXPERIENCE)])
    assert r.matches[0].state == matcher.MET
    assert r.blocking == []


def test_one_level_short_is_partial():
    """실습 보유 × 실무 요구 = 부분(깊이 부족). 걸쳐 있으니 가장 싸게 메울 수 있다."""
    r = matcher.match([_req("Java", reqs.DEPTH_PROFICIENT)])
    m = r.matches[0]
    assert m.state == matcher.PARTIAL and m.reason == matcher.BY_LEVEL
    assert r.to_deepen == [m]


def test_two_levels_short_is_missing():
    """기본 보유 × 실무 요구 = 미충족.

    '기본만 아는 것'을 '실무 요구'에 부분 충족이라고 하면 갭을 과소평가한다.
    """
    r = matcher.match([_req("Python", reqs.DEPTH_PROFICIENT)])
    assert r.matches[0].state == matcher.MISSING


def test_cert_only_is_partial_not_met():
    """★SQLD가 있어도 SQL 실무 요구는 **충족이 아니다.**

    이게 이 도구의 핵심 정직성이다. 자격증을 경험으로 환산하면 '나는 준비됐다'는 거짓
    신호가 나오고, 못 뚫는 이유를 영원히 모른다.
    """
    r = matcher.match([_req("SQL", reqs.DEPTH_EXPERIENCE)])
    m = r.matches[0]
    assert m.state == matcher.PARTIAL and m.reason == matcher.BY_CERT


def test_unowned_and_uncertified_is_missing():
    r = matcher.match([_req("Kafka", reqs.DEPTH_EXPERIENCE)])
    assert r.matches[0].state == matcher.MISSING
    assert r.blocking


def test_empty_requirements_gives_none_not_zero():
    """★요건을 못 뽑았으면 `None`이다. 0이 아니다.

    0을 주면 '요건이 없어서 0점'과 '전혀 안 맞아서 0점'이 구분되지 않는다. 상세 본문
    확보 실패를 '부적합'으로 오인하는 게 이 도구에서 가장 위험한 혼동이다.
    """
    r = matcher.match([])
    assert r.fit_score is None
    assert "요건 미확인" in r.note
    assert "판단 불가" in r.summary()


def test_career_gap_overrides_skill_fit():
    """스킬이 다 맞아도 경력 3년 요구면 낮춘다 — 문턱은 스킬보다 먼저다."""
    r = matcher.match([_req("Java", reqs.DEPTH_EXPERIENCE)],
                      experience_text="경력 3년 이상")
    assert r.career_gap_years == 2
    assert r.fit_score == 1


def test_preferred_gap_does_not_lower_score():
    """우대 미충족은 감점하지 않는다.

    감점하면 기술을 많이 나열한 공고가 전부 부적합이 되어 공고 인플레이션에 끌려간다.
    """
    only_required = matcher.match([_req("Java", reqs.DEPTH_EXPERIENCE)])
    with_preferred = matcher.match([
        _req("Java", reqs.DEPTH_EXPERIENCE),
        _req("Kafka", reqs.DEPTH_PROFICIENT, reqs.KIND_PREFERRED),
    ])
    assert with_preferred.fit_score >= only_required.fit_score - 0
    assert with_preferred.fit_score >= 4


def test_score_discriminates_between_postings():
    """★잘 맞는 공고와 안 맞는 공고의 점수가 달라야 한다.

    처음 구현은 미충족 **개수**로 감점해서(`3 - len(blocking)`) 백엔드 신입 공고와 경력
    3년 공고가 둘 다 1점으로 나왔다. 전부 1점이면 정렬이 안 되고, "적합한 곳 찾기"라는
    목적 자체가 성립하지 않는다. 비율 기반으로 바꾼 이유가 이것이다.
    """
    good = matcher.match([
        _req("Java", reqs.DEPTH_EXPERIENCE),
        _req("Spring Boot", reqs.DEPTH_EXPERIENCE),
        _req("MySQL", reqs.DEPTH_EXPERIENCE),
    ], experience_text="관계없음")
    bad = matcher.match([
        _req("Kubernetes", reqs.DEPTH_PROFICIENT),
        _req("Kafka", reqs.DEPTH_PROFICIENT),
        _req("MSA", reqs.DEPTH_EXPERIENCE),
        _req("Redis", reqs.DEPTH_EXPERIENCE),
    ], experience_text="경력 3년 이상")
    assert good.fit_score > bad.fit_score


def test_missing_required_caps_score_below_full():
    """필수가 하나라도 비면 만점은 없다 — 비율이 높아도 그 하나가 지원을 막는다."""
    r = matcher.match([
        _req("Java", reqs.DEPTH_EXPERIENCE),
        _req("Spring Boot", reqs.DEPTH_EXPERIENCE),
        _req("Kafka", reqs.DEPTH_EXPERIENCE),
    ])
    assert r.blocking
    assert r.fit_score <= 4


def test_thin_posting_gets_a_confidence_note():
    """요건이 적은 공고는 비율이 쉽게 높아진다 — 실력이 맞아서가 아니라 공고가 부실해서다.

    점수를 깎지는 않는다(그건 추측이다). 대신 신뢰도를 표시해 사람이 판단하게 한다.
    """
    r = matcher.match([_req("Java", reqs.DEPTH_EXPERIENCE)])
    assert "신뢰도" in r.note
    assert "⚠️" in matcher.report(r)


def test_gap_skills_are_priority_ordered():
    """막는 것 → 깊이 부족 → 우대 순. 갭 스냅샷 누적이 이 순서를 쓴다."""
    r = matcher.match([
        _req("Kafka", reqs.DEPTH_EXPERIENCE),                        # 미충족(필수)
        _req("Java", reqs.DEPTH_PROFICIENT),                         # 부분(필수)
        _req("Redis", reqs.DEPTH_EXPERIENCE, reqs.KIND_PREFERRED),   # 미충족(우대)
    ])
    assert r.gap_skills() == ["Kafka", "Java", "Redis"]


def test_report_has_no_markdown_table():
    """`md_to_notion.py`가 표를 파싱하지 못한다 (원본 CLAUDE.md의 제약)."""
    r = matcher.match([_req("Kafka", reqs.DEPTH_EXPERIENCE)])
    text = matcher.report(r)
    assert "|" not in text


def test_report_explains_unknown_case():
    text = matcher.report(matcher.match([]))
    assert "판단 불가" in text


def test_matcher_does_not_touch_rules_or_evaluator():
    """★기피 조건(rules.py)과 스펙 적합도는 **다른 축**이라 합치지 않는다.

    스펙이 완벽해도 기피 조건에 걸리면 위험이어야 한다. 두 축을 한 점수로 섞으면 그
    구분이 사라지고, 사용자의 판정 기준이 조용히 희석된다.
    """
    source = open(matcher.__file__, encoding="utf-8").read()
    assert "config.rules" not in source
    assert "import rules" not in source
    assert "evaluator" not in source.split('"""', 2)[2]   # 독스트링 언급은 허용


# ------------------------------------------------- kind는 둘이다 (사용자 결정 2026-10-01)
# 개인층과 통계층이 서로 반대를 요구하므로 한 숫자로 합치지 않는다.
#   kind       보수적 병합 — 갭을 과소평가하지 않는다 (개인층)
#   stat_kind  공고의 명시를 존중 — "필수 11% / 우대 38%"를 지킨다 (통계층)

MIXED_DUTY = ("■ 수행업무\n- REST API 설계 및 개발\n"
              "■ 자격요건 및 우대사항\n"
              "- Java, Spring Boot 기반 서버 개발\n"
              "- Docker 환경에서의 배포 경험")
MIXED_PREFERRED = "- Docker 경험자 우대"


def _by_skill(reqs):
    return {r.skill: r for r in reqs}


def test_declared_preferred_survives_the_merge():
    """공고가 우대라고 적은 것이 본문 언급에 덮여 사라지면 안 된다.

    예전에는 `_dedupe`가 필수를 남기면서 우대 선언을 통째로 버렸다 — 그 상태로 통계를
    내면 공고가 우대로 적은 것이 필수로 집계된다.
    """
    got = _by_skill(reqs.from_texts(duty=MIXED_DUTY, preferred=MIXED_PREFERRED))
    assert got["Docker"].declared_preferred is True
    assert set(got["Docker"].sources) == {"duty", "preferred"}


def test_two_kinds_disagree_and_that_is_correct():
    got = _by_skill(reqs.from_texts(duty=MIXED_DUTY, preferred=MIXED_PREFERRED))
    assert got["Docker"].kind == reqs.KIND_REQUIRED, "개인층은 보수적이어야 한다"
    assert got["Docker"].stat_kind == reqs.KIND_PREFERRED, "통계층은 명시를 따른다"
    # 본문에만 있는 스킬은 둘이 같다 — 분기는 '명시 우대가 있었을 때'만 생긴다
    assert got["Java"].kind == got["Java"].stat_kind == reqs.KIND_REQUIRED


def test_personal_layer_is_unchanged_by_the_split():
    """`kind`를 안 건드렸으므로 적합도·갭은 예전과 똑같이 나와야 한다."""
    items = reqs.from_texts(duty=MIXED_DUTY, preferred=MIXED_PREFERRED)
    assert all(r.is_required == (r.kind == reqs.KIND_REQUIRED) for r in items)
    assert _by_skill(items)["Docker"].is_required is True


def test_implied_requirement_inherits_the_declaration():
    """`Spring Boot 우대`에서 끌어온 `Java`를 통계가 필수로 세면 왜곡이 되살아난다."""
    got = _by_skill(reqs.from_texts(preferred="- Spring Boot 경험자 우대"))
    assert got["Java"].declared_preferred is True
    assert got["Java"].stat_kind == reqs.KIND_PREFERRED


def test_license_default_is_inference_not_declaration():
    """`자격 면허` 항목의 기본값 우대는 upstream의 추정이지 공고의 선언이 아니다.

    이걸 선언으로 세면 통계가 반대쪽으로 틀어진다 — 우대를 과대 집계한다.
    """
    got = _by_skill(reqs.from_texts(license_="정보처리산업기사"))
    if got:
        r = next(iter(got.values()))
        assert r.declared_preferred is False, "추정을 명시로 승격하지 말 것"
