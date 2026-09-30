"""ROI 계산식 테스트 — 이 파일이 계산식의 정의를 고정한다.

`docs/career-plan.md` §2가 ROI를 최종 산출물이라고 선언했으므로, 여기가 조용히 깨지면
도구가 내는 유일한 행동 지침이 틀린다. `config/rules.py`와 같은 지위로 실제 테스트를
유지한다.

⚠️ **upstream에는 대응 테스트가 없다.** upstream의 `Score`는 LLM이 주관적으로 매기는
값이라 테스트할 계산식이 없었다. 이 파일은 전부 신규다.
"""
from __future__ import annotations

import pytest

from config import profile
from src import roi
from src.requirements import DEPTH_EXPERIENCE, DEPTH_PROFICIENT, KIND_PREFERRED, KIND_REQUIRED, Requirement


def req(skill: str, depth: int = DEPTH_EXPERIENCE, kind: str = KIND_REQUIRED) -> Requirement:
    return Requirement(skill=skill, kind=kind, depth=depth, field="duty", evidence="t")


def posting(pid: str, *reqs: Requirement, exp: str = "신입", ok: bool = True) -> roi.PostingReqs:
    return roi.PostingReqs(posting_id=pid, requirements=tuple(reqs),
                           experience_text=exp, requirements_ok=ok)


# 프로필 전제(config/profile.py): Java·Spring Boot 레벨 2, Python·React 1, SQL·Linux·AWS 0
# 아래 픽스처는 그 전제 위에서 의미를 갖는다.

@pytest.fixture
def pop() -> roi.Population:
    """5건. Java는 전부 충족, MySQL이 3건을 막고, Docker가 1건을 막는다."""
    return roi.population([
        posting("a", req("Java")),                           # 지원 가능
        posting("b", req("Java"), req("MySQL")),             # MySQL이 막음
        posting("c", req("Java"), req("MySQL")),             # MySQL이 막음
        posting("d", req("Java"), req("MySQL"), req("JPA")),  # MySQL+JPA가 막음
        posting("e", req("Java"), req("Docker")),            # Docker가 막음
    ])


# ============================================================ 모집단

def test_requirements_not_ok_is_excluded_and_counted():
    """★요건 미확보는 '안 맞는 것'이 아니라 '모르는 것'이다.

    포함하면 전부 미충족으로 세어져 base가 내려가고 gain이 부풀려진다. 조용히 빼면 숫자를
    설명할 수 없으므로 세어서 함께 돌려준다.
    """
    p = roi.population([posting("a", req("Java")),
                        posting("b", ok=False)])
    assert p.size == 1
    assert p.dropped_no_requirements == 1


def test_career_blocked_is_excluded_and_counted():
    """★스킬로 열 수 없는 공고는 모집단에서 뺀다.

    3년 경력은 아무리 공부해도 만들 수 없다. 포함하면 ROI 천장이 낮게 고정되고
    "아무리 해도 안 열린다"는 잘못된 신호가 된다.
    """
    p = roi.population([posting("a", req("Java")),
                        posting("b", req("Java"), exp="경력 3년 이상")])
    assert p.size == 1
    assert p.dropped_career == 1


def test_empty_requirements_counts_as_unknown():
    p = roi.population([posting("a")])
    assert p.size == 0 and p.dropped_no_requirements == 1


# ============================================================ base

def test_base_rate_counts_only_fully_eligible(pop: roi.Population):
    """지원 가능 = 필수 미충족 0개. 5건 중 a만."""
    assert pop.size == 5
    assert roi.eligible_rate(pop) == pytest.approx(1 / 5)


def test_base_rate_is_zero_for_empty_population():
    assert roi.eligible_rate(roi.Population()) == 0.0


def test_preferred_gap_does_not_block():
    """우대 미충족은 지원을 막지 않는다 — blocking은 필수만 센다."""
    p = roi.population([posting("a", req("Java"), req("Kafka", kind=KIND_PREFERRED))])
    assert roi.eligible_rate(p) == 1.0


# ============================================================ gain

def test_gain_of_blocking_skill_is_positive(pop: roi.Population):
    """MySQL을 채우면 b·c가 열린다(d는 JPA가 남아 안 열린다)."""
    items = roi.rank_single(pop)
    mysql = next(i for i in items if i.skills == ("MySQL",))
    assert mysql.new_rate == pytest.approx(3 / 5)   # a + b + c
    assert mysql.gain_pp == pytest.approx(40.0)


def test_gain_of_non_blocking_skill_is_excluded(pop: roi.Population):
    """아무 공고도 막지 않는 스킬은 순위에 나오지 않는다(gain 0)."""
    names = {i.skills[0] for i in roi.rank_single(pop)}
    assert "Kafka" not in names
    assert "Java" not in names      # 이미 충족이므로 후보가 아니다


def test_candidates_are_only_blocking_skills(pop: roi.Population):
    """★후보를 막고 있는 스킬로 줄이는 것이 전수 탐색을 가능하게 한다."""
    assert set(roi.candidates(pop)) == {"MySQL", "JPA", "Docker"}


def test_ranking_is_by_gain_desc(pop: roi.Population):
    items = roi.rank_single(pop)
    assert [i.skills[0] for i in items][:2] == ["MySQL", "Docker"]
    assert items[0].gain >= items[-1].gain


def test_gain_never_negative(pop: roi.Population):
    """스킬을 올려서 지원 가능 공고가 줄어들 수는 없다(단조성)."""
    for item in roi.rank_single(pop):
        assert item.gain >= 0


def test_global_profile_is_not_mutated(pop: roi.Population):
    """★가상 프로필 계산이 전역 프로필을 변조하지 않는다.

    변조 방식은 예외가 나면 그 상태가 다음 계산까지 새서, 이후 모든 판정이 조용히 틀린다.
    """
    before = dict(profile.SKILL_LEVELS)
    roi.rank_single(pop)
    roi.rank_bundles(pop, size=2)
    assert dict(profile.SKILL_LEVELS) == before
    assert profile.level("MySQL") == profile.LEVEL_NONE


# ============================================================ 목표 레벨

def test_practice_target_does_not_satisfy_work_depth():
    """실무(깊이 3) 요구는 레벨 2로 못 넘는다 — 두 시나리오를 나란히 내는 이유."""
    p = roi.population([posting("a", req("Docker", depth=DEPTH_PROFICIENT))])
    at2 = roi.rank_single(p, target_level=roi.TARGET_PRACTICE)
    at3 = roi.rank_single(p, target_level=roi.TARGET_WORK)
    assert at2 == []                                    # 레벨 2로는 안 열린다
    assert at3 and at3[0].new_rate == pytest.approx(1.0)


# ============================================================ 묶음·시너지

def test_bundle_synergy_is_positive_when_co_required():
    """★MySQL과 JPA가 같은 공고에서 함께 요구되면 묶음 gain이 단독 합보다 크다.

    이게 전수 탐색을 하는 이유다. greedy 근사로는 이 조합이 안 나온다.
    """
    p = roi.population([
        posting("a", req("MySQL"), req("JPA")),
        posting("b", req("MySQL"), req("JPA")),
    ])
    assert roi.rank_single(p) == []          # 단독으로는 아무것도 안 열린다
    bundles = roi.rank_bundles(p, size=2)
    # 묶음은 집합이다. 튜플 순서는 사전 선언 순서를 따르므로 순서로 단정하지 않는다.
    assert bundles and set(bundles[0].skills) == {"MySQL", "JPA"}
    assert bundles[0].new_rate == pytest.approx(1.0)
    assert bundles[0].synergy == pytest.approx(1.0)


def test_bundle_without_synergy_is_omitted():
    """단독으로 각각 열리는 조합은 묶음 리포트에 내지 않는다 — 정보가 없다."""
    p = roi.population([posting("a", req("MySQL")), posting("b", req("Docker"))])
    assert {i.skills[0] for i in roi.rank_single(p)} == {"MySQL", "Docker"}
    assert roi.rank_bundles(p, size=2) == []


def test_bundle_size_is_bounded():
    """조합 폭발을 막는 상한이 있다."""
    p = roi.population([posting("a", req("MySQL"))])
    with pytest.raises(ValueError):
        roi.rank_bundles(p, size=roi.MAX_BUNDLE + 1)
    with pytest.raises(ValueError):
        roi.rank_bundles(p, size=1)


# ============================================================ 신뢰도·출력

def test_small_population_warns():
    """모집단이 작으면 순위를 신뢰할 수 없다. 점수를 깎지는 않고 경고만 한다."""
    p = roi.population([posting("a", req("Java"))])
    assert "모집단" in p.confidence_note()


def test_high_unknown_ratio_warns():
    """요건 미확보가 20%를 넘으면 상세 수집 성공률을 먼저 보라고 알린다."""
    p = roi.population([posting("a", req("Java"))] + [posting(f"x{i}", ok=False)
                                                     for i in range(3)])
    assert "요건 미확보" in p.confidence_note()


def test_no_warning_when_healthy():
    p = roi.population([posting(f"p{i}", req("Java")) for i in range(roi.MIN_POPULATION)])
    assert p.confidence_note() == ""


def test_state_of_distinguishes_cheap_from_scratch():
    """난이도 가중치 대신 현재 상태를 낸다 — 레벨 1→2가 0→2보다 싸다는 게 보여야 한다."""
    item = roi.RoiItem(skills=("Python",), target_level=3, base_rate=0, new_rate=0.1)
    assert "절반 와 있다" in item.state_of("Python")      # 레벨 1 보유
    assert "자격증만" in item.state_of("SQL")             # SQLD 보유, 레벨 0
    assert "처음부터" in item.state_of("Kafka")


def test_report_has_no_markdown_table(pop: roi.Population):
    """`md_to_notion.py`가 표를 파싱하지 못한다."""
    assert "|" not in roi.report(pop)


def test_report_shows_excluded_counts():
    """왜 이 숫자인지 설명할 수 있어야 한다 — 제외분을 리포트에 낸다."""
    p = roi.population([
        posting("a", req("Java")),
        posting("b", req("Java"), exp="경력 5년 이상"),
        posting("c", ok=False),
    ])
    text = roi.report(p)
    assert "경력 요건으로 막힌" in text
    assert "요건 미확보" in text


def test_report_collapses_identical_scenarios():
    """요구 깊이가 전부 '경험'이면 레벨 3까지 올려도 결과가 같다 — 표를 두 번 내지 않는다."""
    p = roi.population([posting("a", req("MySQL")), posting("b", req("Java"))])
    text = roi.report(p)
    assert text.count("## 실습") == 1
    assert "## 실무" not in text
    assert "실무까지 올려도 결과가 같다" in text


def test_report_shows_both_scenarios_when_they_differ():
    """깊이 3 요구가 섞여 있으면 두 시나리오가 갈리므로 둘 다 낸다."""
    p = roi.population([posting("a", req("MySQL")),
                        posting("b", req("Docker", depth=DEPTH_PROFICIENT))])
    text = roi.report(p)
    assert "## 실무" in text
