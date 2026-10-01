"""ROI — "이 스킬을 채우면 지원 가능한 공고가 몇 %p 늘어나는가".

`docs/career-plan.md` §2가 최종 산출물이라고 선언한 값이다. 이 모듈이 그 정의다.

**upstream에는 이런 계산이 없었다.** upstream의 `Score`는 LLM이 프롬프트 지시("1~5 정수,
5가 가장 적합")로 **주관적으로 매기는 값**이고 `evaluator._clamp_score`는 범위만 자른다.
집계 개념은 "적합 몇 건" 수준의 건수 통계뿐이었다. 요건 분포·비율·ROI는 전부 신규다.

그래서 `Score`(LLM 주관, Verdict축의 보조 정렬키)와 `FitScore`(코드 계산, 스펙축)와
여기서 내는 `ROI`(코퍼스 전체에 대한 한계 효용)는 **서로 다른 세 값**이다. 섞지 말 것.

───────────────────────────────────────────────────────────────────────────────
## 정의

    모집단 P      최근 WINDOW_WEEKS주 · 목표 직군 1개 · 요건 확보됨 · **경력 요건 통과**
    지원가능(p)   **필수 요건이 전부 충족(MET)** — 미충족도 부분충족도 없다
    base          |{p ∈ P : 지원가능(p)}| / |P|
    gain(S)       S를 채운 가상 프로필의 비율 − base

**근사하지 않는다.** 후보를 "현재 막고 있는 스킬"로 줄이면 전수 탐색이 밀리초로 끝난다
(300공고 × 후보 20개 = 6,000회 평가). greedy로 근사하면 시너지 조합을 놓치는데, **그
시너지가 이 리포트의 핵심 가치**다("MySQL+JPA를 같이 하면 단독 합보다 크다").

## 설계 결정과 이유

**① 분자는 "필수 전부 충족"이고 `FitScore`가 아니다.**
`FitScore`는 충족 **비율**이라 "4점인데 필수 하나가 빠진" 상태가 가능하다. ROI가 답해야
하는 질문은 "지원할 수 있나"이고 그건 이진값이다.

⚠️ **부분충족(깊이 1 부족)도 통과로 세지 않는다.** 처음에는 `matcher.blocking == 0`
(= 미충족 0개)으로 짰는데, 그러면 `Docker 운영 경험` 요구에 실습 레벨이 통과가 되어
**레벨 2 시나리오와 레벨 3 시나리오가 구분되지 않았다**(테스트로 잡았다). 그리고
career-plan의 "실패 방향은 갭을 과소평가하지 않는 쪽"과 어긋난다 — 요구 깊이를 못 맞춘
것을 지원 가능으로 세면 "지원했는데 다 떨어진다"로 이어지는 낙관적 숫자가 나온다.
그래서 `blocking == 0 AND to_deepen == 0`, 즉 **필수는 전부 MET**이어야 한다.

**② 경력으로 막힌 공고는 모집단에서 뺀다.**
스킬을 아무리 채워도 3년 경력은 만들 수 없다. 포함하면 ROI 천장이 낮게 고정되고
"아무리 해도 안 열린다"는 잘못된 신호가 된다. 대신 **제외 비율을 리포트에 표시**해서
"경력 때문에 막힌 시장이 몇 %인지"를 따로 보여준다.

**③ 요건 미확보 공고도 모집단에서 뺀다.**
`requirements_ok=false`는 상세 본문을 못 받은 것이지 안 맞는 게 아니다. 포함하면 전부
미충족으로 세어져 base가 내려가고 gain이 부풀려진다. 이것도 제외 비율을 표시한다.

**④ 시나리오는 "요구를 통과하는 최소 레벨까지"가 아니라 고정 목표 레벨이다.**
공고마다 요구 깊이가 달라서 "통과하는 최소"는 공고별로 다르다. 그래서 **실습(2)과
실무(3) 두 시나리오를 나란히 낸다** — "프로젝트 하나 만들면 얼마, 실무 수준까지 가면
얼마"가 보여야 투자 판단이 된다.

**⑤ 난이도 가중치를 만들지 않는다.**
"Kubernetes는 어렵다"를 숫자로 만들면 그건 추측이다. 대신 **현재 상태**(레벨 0인가,
1이라 절반 와 있는가, 자격증만 있는가)를 같이 내서 사람이 판단하게 한다. 레벨 1→2가
0→2보다 싸다는 것은 표에서 바로 보인다.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from itertools import combinations

from config import profile
from src import matcher
from src.requirements import Requirement, required_years

# 모집단 창. 8주는 표본(수백 건)과 시장 최신성의 타협이다. 공채 사이클 하나에는 못 미치므로
# **변화율 지표로 쓰지 말 것** — 그건 9개월 이상 쌓인 뒤의 얘기다(career-plan §2).
WINDOW_WEEKS = 8

# 이 미만이면 비율의 변동이 커서 순위를 신뢰할 수 없다. 점수를 깎지는 않고 경고만 한다.
MIN_POPULATION = 30

# 묶음 탐색 상한. 후보를 blocking 스킬로 줄였으므로 3까지 전수가 가능하다. 4 이상은
# 계산은 되지만 "한 번에 네 개를 배운다"는 계획이 현실적이지 않아 리포트 가치가 없다.
MAX_BUNDLE = 3

# 기본 시나리오 목표 레벨
TARGET_PRACTICE = profile.LEVEL_PRACTICE   # 2 — 프로젝트 하나 만들면 도달
TARGET_WORK = profile.LEVEL_WORK           # 3 — 실무 수준


@dataclass(frozen=True)
class PostingReqs:
    """ROI 계산에 필요한 공고 1건의 최소 정보.

    저장 형식(SQLite / JSONL)이 아직 미결정이므로 **이 모듈은 저장소를 모른다.**
    적재층이 이 형태로 넘겨주면 된다.
    """
    posting_id: str
    requirements: tuple[Requirement, ...]
    experience_text: str = ""
    requirements_ok: bool = True


@dataclass
class Population:
    """모집단과, 왜 무엇이 빠졌는지."""
    postings: list[PostingReqs] = field(default_factory=list)
    dropped_no_requirements: int = 0
    dropped_career: int = 0

    @property
    def size(self) -> int:
        return len(self.postings)

    @property
    def total_seen(self) -> int:
        return self.size + self.dropped_no_requirements + self.dropped_career

    def confidence_note(self) -> str:
        """신뢰도 경고. 빈 문자열이면 문제 없음."""
        notes = []
        if self.size < MIN_POPULATION:
            notes.append(f"모집단 {self.size}건 — {MIN_POPULATION}건 미만이라 비율 변동이 크다")
        if self.total_seen and self.dropped_no_requirements / self.total_seen > 0.2:
            pct = self.dropped_no_requirements / self.total_seen * 100
            notes.append(f"요건 미확보로 {pct:.0f}% 제외 — 상세 수집 성공률을 먼저 볼 것")
        return " / ".join(notes)


def population(records: Iterable[PostingReqs], *,
               max_years: int | None = None) -> Population:
    """모집단 구성. **요건 미확보와 경력 초과를 빼고, 왜 빠졌는지 센다.**

    둘을 조용히 포함하면 base가 내려가 gain이 부풀려지고, 조용히 빼면 "왜 이 숫자인지"를
    설명할 수 없다. 그래서 세어서 함께 돌려준다.
    """
    limit = profile.MAX_REQUIRED_YEARS if max_years is None else max_years
    out = Population()
    for rec in records:
        if not rec.requirements_ok or not rec.requirements:
            out.dropped_no_requirements += 1
            continue
        years = required_years(rec.experience_text) if rec.experience_text else None
        if years is not None and years > limit:
            out.dropped_career += 1
            continue
        out.postings.append(rec)
    return out


def _level_with(overrides: dict[str, int]) -> Callable[[str], int]:
    """가상 프로필의 레벨 조회 함수. **전역 프로필을 변조하지 않는다.**

    변조 방식은 예외가 나면 그 상태가 다음 계산까지 새기 때문에 쓰지 않는다.
    """
    def level_of(skill: str) -> int:
        base = profile.level(skill)
        return max(base, overrides.get(skill, base))
    return level_of


def is_eligible(rec: PostingReqs, level_of: Callable[[str], int]) -> bool:
    """지원 가능한가 = **필수 요건이 전부 충족(MET)**인가.

    `blocking`(필수·미충족)과 `to_deepen`(필수·부분충족)이 둘 다 비어야 한다. 부분충족을
    통과로 세면 요구 깊이를 못 맞춘 공고가 지원 가능으로 잡힌다(모듈 독스트링 ① 참조).
    우대 요건은 지원을 막지 않으므로 보지 않는다.
    """
    result = matcher.match(list(rec.requirements),
                           experience_text=rec.experience_text, level_of=level_of)
    return not result.blocking and not result.to_deepen


def eligible_rate(pop: Population, overrides: dict[str, int] | None = None) -> float:
    """가상 프로필(기본값은 현재 프로필)에서의 지원 가능 비율 0.0~1.0."""
    if pop.size == 0:
        return 0.0
    level_of = _level_with(overrides or {})
    hits = sum(1 for rec in pop.postings if is_eligible(rec, level_of))
    return hits / pop.size


def candidates(pop: Population) -> list[str]:
    """ROI 후보 스킬 = **현재 지원 가능을 막고 있는 스킬**.

    미충족(`blocking`)과 **부분충족(`to_deepen`) 둘 다** 포함한다 — 지원 가능 기준이
    "필수 전부 MET"이므로 깊이가 한 단계 부족한 것도 막고 있는 것이다.

    아무 공고도 막지 않는 스킬을 채워도 gain이 0인 것은 자명하므로 사전 전체(50개)를 돌
    필요가 없다. 보통 10~20개로 줄고, 그래서 묶음 전수 탐색이 가능해진다.
    """
    level_of = _level_with({})
    blocking: set[str] = set()
    for rec in pop.postings:
        result = matcher.match(list(rec.requirements),
                               experience_text=rec.experience_text, level_of=level_of)
        blocking.update(m.requirement.skill for m in result.blocking)
        blocking.update(m.requirement.skill for m in result.to_deepen)
    # 출력 안정성을 위해 사전 선언 순서로 정렬
    from config import skills as _skills
    return [s for s in _skills.ALL_SKILLS if s in blocking]


@dataclass(frozen=True)
class RoiItem:
    """ROI 1건. `skills`가 하나면 단독, 여러 개면 묶음."""
    skills: tuple[str, ...]
    target_level: int
    base_rate: float
    new_rate: float
    synergy: float = 0.0        # 묶음일 때만. gain − Σ(단독 gain)

    @property
    def gain(self) -> float:
        return self.new_rate - self.base_rate

    @property
    def gain_pp(self) -> float:
        """퍼센트 포인트."""
        return self.gain * 100

    def state_of(self, skill: str) -> str:
        """현재 상태 — 난이도 가중치 대신 사람이 판단할 재료."""
        have = profile.level(skill)
        if have > 0:
            return f"레벨 {have}({profile.LEVEL_NAMES[have]}) — 절반 와 있다"
        from config import skills as _skills
        if skill in _skills.certified_skills(list(profile.CERTS)):
            return "자격증만 — 개념은 있다"
        return "처음부터"

    def label(self) -> str:
        names = " + ".join(self.skills)
        tail = f"  (시너지 {self.synergy * 100:+.1f}%p)" if len(self.skills) > 1 else ""
        return (f"{names} → 레벨 {self.target_level}: "
                f"{self.base_rate * 100:.0f}% → {self.new_rate * 100:.0f}% "
                f"({self.gain_pp:+.1f}%p){tail}")


def rank_single(pop: Population, *, target_level: int = TARGET_PRACTICE,
                limit: int | None = None) -> list[RoiItem]:
    """단독 스킬 ROI 순위 (gain 내림차순). gain이 0인 것은 제외한다."""
    base = eligible_rate(pop)
    out = []
    for skill in candidates(pop):
        rate = eligible_rate(pop, {skill: target_level})
        item = RoiItem(skills=(skill,), target_level=target_level,
                       base_rate=base, new_rate=rate)
        if item.gain > 0:
            out.append(item)
    out.sort(key=lambda i: (-i.gain, i.skills))
    return out[:limit] if limit else out


def rank_bundles(pop: Population, *, target_level: int = TARGET_PRACTICE,
                 size: int = 2, limit: int | None = None) -> list[RoiItem]:
    """묶음 ROI. **시너지가 양수인 것만** 낸다.

    시너지 = gain(묶음) − Σ gain(단독). 양수면 두 스킬이 같은 공고에서 함께 요구된다는
    뜻이고, 그때만 "같이 하라"는 조언이 단독 순위보다 정보를 더 준다. 0이나 음수면
    단독 순위를 보면 되므로 리포트를 늘릴 이유가 없다.
    """
    if not 2 <= size <= MAX_BUNDLE:
        raise ValueError(f"묶음 크기는 2~{MAX_BUNDLE}만 지원한다 (요청 {size})")
    base = eligible_rate(pop)
    solo = {i.skills[0]: i.gain
            for i in rank_single(pop, target_level=target_level)}
    # ⚠️ **후보를 solo gain > 0으로 걸러서는 안 된다.** 시너지의 정의가 "단독으로는 안
    # 열리는데 함께 채우면 열린다"이므로, 그 필터는 찾으려는 케이스를 정확히 제외한다.
    # 처음 구현이 그 버그였고 `test_bundle_synergy_is_positive_when_co_required`가 잡았다.
    pool = candidates(pop)
    out = []
    for combo in combinations(pool, size):
        rate = eligible_rate(pop, {s: target_level for s in combo})
        gain = rate - base
        synergy = gain - sum(solo.get(s, 0.0) for s in combo)
        if synergy > 1e-9:
            out.append(RoiItem(skills=combo, target_level=target_level,
                               base_rate=base, new_rate=rate, synergy=synergy))
    out.sort(key=lambda i: (-i.synergy, -i.gain, i.skills))
    return out[:limit] if limit else out


def report(pop: Population, *, top: int = 5) -> str:
    """사람이 읽는 ROI 리포트.

    ⚠️ **마크다운 표를 쓰지 않는다** — `md_to_notion.py`가 표를 파싱하지 못한다.
    """
    lines: list[str] = []
    base = eligible_rate(pop)
    lines.append(f"**지원 가능 {base * 100:.0f}%** (모집단 {pop.size}건)")
    if pop.dropped_career:
        pct = pop.dropped_career / pop.total_seen * 100
        lines.append(f"- 경력 요건으로 막힌 공고 {pop.dropped_career}건 ({pct:.0f}%) — "
                     "스킬로는 열 수 없어 모집단에서 제외했다")
    if pop.dropped_no_requirements:
        lines.append(f"- 요건 미확보 {pop.dropped_no_requirements}건 — 상세 수집 실패분")
    note = pop.confidence_note()
    if note:
        lines.append("")
        lines.append(f"> ⚠️ {note}")

    practice = rank_single(pop, target_level=TARGET_PRACTICE, limit=top)
    work = rank_single(pop, target_level=TARGET_WORK, limit=top)
    # 두 시나리오가 같으면 한 번만 낸다. 그 주 공고의 요구 깊이가 대부분 '경험'이면
    # 레벨 3까지 올려도 열리는 공고가 늘지 않는다 — 같은 표를 두 번 내면 노이즈다.
    same = [(i.skills, i.new_rate) for i in practice] == [(i.skills, i.new_rate) for i in work]
    blocks = [(practice, "실습(레벨 2)까지 — 실무까지 올려도 결과가 같다" if same
               else "실습(레벨 2)까지")]
    if not same:
        blocks.append((work, "실무(레벨 3)까지"))

    for items, title in blocks:
        if not items:
            continue
        lines.append("")
        lines.append(f"## {title}")
        for n, item in enumerate(items, 1):
            lines.append(f"{n}. {item.label()}")
            lines.append(f"   - 현재: {item.state_of(item.skills[0])}")

    bundles = rank_bundles(pop, size=2, limit=3)
    if bundles:
        lines.append("")
        lines.append("## 같이 하면 더 열리는 조합")
        for item in bundles:
            lines.append(f"- {item.label()}")
    return "\n".join(lines)
