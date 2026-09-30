"""요건 × 내 스펙 → (적합도, 부족한 것).

이 프로젝트가 원본 career-scout에서 갈라지는 지점이다. 원본의 판정은 입력이 하나였다
(공고를 고정된 기준에 비춘다). 여기서는 `공고 × config/profile.py`의 2입력 함수가 되고,
출력이 **적합도**와 **부족한 것** 둘이다. 사용자가 원한 것이 정확히 그 둘이다.

설계 원칙 — 원본 `CLAUDE.md`에서 그대로 가져온다.

  **규칙이 LLM을 이긴다.** "Docker를 요구하는데 내 프로필에 Docker가 없다"는 사전 매칭으로
  확정되는 사실이고, 모델이 뒤집을 여지를 주면 같은 공고가 실행마다 다르게 나온다. 이
  모듈은 LLM을 호출하지 않는다. `evaluator`는 이 결과를 **힌트로 받아** 설명을 쓰고,
  코드가 최종값을 덮어쓴다 — `signals.py` → `evaluator.py` 관계와 같다.

⚠️ **이 모듈은 `config/rules.py`의 판정을 대신하지 않는다.** `rules.py`는 "기피 조건"
(사용자가 피하려는 근무형태·회사)의 정본이고, 여기는 "스펙이 맞는가"다. 둘은 다른 축이라
합치면 안 된다 — 스펙이 완벽해도 기피 조건에 걸리면 위험이어야 한다.

⚠️ **실패 방향이 정해져 있다.** 갭을 **과소평가하지 않는 쪽**으로 기울인다. 갭을 크게
보면 이미 아는 걸 또 공부하는 손해지만, 작게 보면 못 뚫는 이유를 영원히 모른다.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from config import profile, skills
from src.requirements import (
    DEPTH_NAMES, KIND_REQUIRED, Requirement, required_years,
)

# --- 매칭 상태 --------------------------------------------------------------
MET = "충족"
PARTIAL = "부분"
MISSING = "미충족"

# 부분 충족의 사유
BY_CERT = "자격증만"          # 자격증이 증빙하지만 실물 경험 미확인
BY_LEVEL = "깊이 부족"        # 보유하지만 요구 깊이보다 한 단계 낮음


@dataclass(frozen=True)
class SkillMatch:
    requirement: Requirement
    state: str                 # MET | PARTIAL | MISSING
    reason: str = ""           # PARTIAL일 때 BY_CERT | BY_LEVEL
    have_level: int = 0

    def label(self) -> str:
        req = self.requirement
        have = profile.LEVEL_NAMES.get(self.have_level, "?")
        want = DEPTH_NAMES.get(req.depth, "?")
        tail = f" ({self.reason})" if self.reason else ""
        return f"{req.skill}: 요구 {want} / 보유 {have}{tail}"


@dataclass
class MatchResult:
    """한 공고에 대한 매칭 결과.

    `fit_score`가 `None`이면 **요건을 확인하지 못한 것**이다(상세 본문 미확보 등).
    0점이 아니라 None이어야 한다 — 조용히 0을 주면 "요건이 없어서 0"과 "안 맞아서 0"이
    구분되지 않고, 그게 이 도구에서 제일 위험한 혼동이다.
    """
    matches: list[SkillMatch] = field(default_factory=list)
    fit_score: int | None = None          # 1~5, None이면 판단 불가
    career_gap_years: int | None = None   # 요구 경력이 내 상한을 넘으면 초과 연수
    note: str = ""

    # ---- 뷰 ----
    def by_state(self, state: str) -> list[SkillMatch]:
        return [m for m in self.matches if m.state == state]

    @property
    def blocking(self) -> list[SkillMatch]:
        """**필수인데 미충족** — 지원을 막는 것들. 갭 목록의 1순위."""
        return [m for m in self.matches
                if m.state == MISSING and m.requirement.is_required]

    @property
    def to_deepen(self) -> list[SkillMatch]:
        """필수인데 부분 충족 — 이미 걸쳐 있어 **가장 싸게 메울 수 있는** 것들."""
        return [m for m in self.matches
                if m.state == PARTIAL and m.requirement.is_required]

    @property
    def nice_to_have(self) -> list[SkillMatch]:
        return [m for m in self.matches
                if m.state != MET and not m.requirement.is_required]

    def gap_skills(self) -> list[str]:
        """부족한 스킬 이름 — 우선순위 순. 주간 '갭 스냅샷' 누적에 쓴다."""
        seen: list[str] = []
        for group in (self.blocking, self.to_deepen, self.nice_to_have):
            for m in group:
                if m.requirement.skill not in seen:
                    seen.append(m.requirement.skill)
        return seen

    def summary(self) -> str:
        if self.fit_score is None:
            return f"판단 불가 — {self.note or '요건 미확인'}"
        met = len(self.by_state(MET))
        return (f"적합도 {self.fit_score}/5 · 충족 {met}/{len(self.matches)}"
                f" · 막는 것 {len(self.blocking)}개")


LevelOf = Callable[[str], int]


def _state(req: Requirement, certified: set[str],
           level_of: LevelOf) -> tuple[str, str, int]:
    """한 요건의 (상태, 사유, 보유레벨).

    판정 순서가 의미를 갖는다.
      1. 보유 레벨이 요구 깊이 이상 → 충족
      2. 한 단계 부족 → 부분(깊이 부족). 두 단계 이상은 미충족으로 본다 — '기본'만 아는
         것을 '실무 요구'에 부분 충족이라고 하면 갭을 과소평가한다.
      3. 레벨은 0인데 자격증이 증빙 → 부분(자격증만). **자격증은 레벨을 채우지 않는다.**
      4. 그 외 → 미충족

    `level_of`는 보유 레벨을 돌려주는 함수다. 기본값은 `profile.level`이고, **ROI 계산이
    "이 스킬을 채웠다면" 가상 프로필로 재평가하려고 이 자리를 주입한다**(`src/roi.py`).
    전역 프로필을 임시로 변조하는 대신 함수를 갈아끼우는 이유는, 변조가 실패하면 그
    상태가 다음 계산까지 새기 때문이다.
    """
    have = level_of(req.skill)
    if have >= req.depth:
        return MET, "", have
    if have > 0 and req.depth - have == 1:
        return PARTIAL, BY_LEVEL, have
    if have == 0 and req.skill in certified:
        return PARTIAL, BY_CERT, have
    return MISSING, "", have


# 필수 요건이 이 개수 미만이면 적합도를 신뢰하기 어렵다(공고가 상세하지 않은 것).
THIN_REQUIRED = 3


def _score(matches: list[SkillMatch], career_gap: int | None) -> int:
    """적합도 1~5. **충족 비율**로 낸다.

    ⚠️ 처음에는 미충족 **개수**로 감점했는데(`3 - len(blocking)`) 변별력이 없었다 —
    백엔드 신입 공고와 경력 3년 공고가 둘 다 1점으로 나와 정렬이 불가능했다. 도구의
    목적이 "적합한 곳 찾기"인데 전부 1점이면 찾을 수가 없다. 요건이 많은 공고가 자동으로
    바닥을 치는 것도 공고 인플레이션에 그대로 끌려가는 것이다.

    그래서 비율로 바꿨다. 부분 충족은 0.5점으로 센다 — 걸쳐 있는 것과 아예 없는 것은
    준비 비용이 다르다.

    우대는 점수를 깎지 않는다. 깎으면 기술을 많이 나열한 공고가 전부 부적합이 된다.
    """
    required = [m for m in matches if m.requirement.is_required]
    if not required:
        base = 4          # 필수가 없고 우대만 있는 공고 — 문턱이 낮다
    else:
        earned = sum(1.0 if m.state == MET else 0.5 if m.state == PARTIAL else 0.0
                     for m in required)
        base = 1 + round((earned / len(required)) * 4)
        if any(m.state == MISSING for m in required):
            # 필수가 하나라도 비면 만점을 줄 수 없다. 비율이 높아도 그 하나가 막는다.
            base = min(base, 4)
    if career_gap:
        # 경력 문턱은 스킬보다 먼저다. 스킬이 다 맞아도 3년을 요구하면 지원이 안 된다.
        base = min(base, 1 if career_gap >= 2 else 2)
    return max(1, min(5, base))


def match(requirements: list[Requirement], *, experience_text: str = "",
          level_of: LevelOf | None = None,
          note_when_empty: str = "요건 미확인 — 상세 본문 미확보") -> MatchResult:
    """요건 목록을 내 스펙에 비춘다.

    `experience_text`는 공고의 경력 항목(워크넷 목록의 `경력무관 학력무관` 등). 비면
    경력 갭은 판단하지 않는다(모르는 것을 0으로 치지 않는다).
    """
    if not requirements:
        return MatchResult(note=note_when_empty)

    certified = skills.certified_skills(list(profile.CERTS))
    level_of = level_of or profile.level
    matches: list[SkillMatch] = []
    for req in requirements:
        state, reason, have = _state(req, certified, level_of)
        matches.append(SkillMatch(requirement=req, state=state, reason=reason,
                                  have_level=have))

    years = required_years(experience_text) if experience_text else None
    career_gap = None
    if years is not None and years > profile.MAX_REQUIRED_YEARS:
        career_gap = years - profile.MAX_REQUIRED_YEARS

    # 요건이 적은 공고는 비율이 쉽게 높아진다 — 실력이 맞아서가 아니라 공고가 부실해서다.
    # 점수를 깎지는 않고(추측이 되므로) 신뢰도만 표시한다. 사람이 판단할 재료를 준다.
    required_count = sum(1 for m in matches if m.requirement.is_required)
    note = ""
    if required_count < THIN_REQUIRED:
        note = (f"필수 요건 {required_count}건뿐 — 공고가 상세하지 않아 적합도 신뢰도가"
                " 낮습니다. 원문을 직접 확인하세요")

    return MatchResult(matches=matches, fit_score=_score(matches, career_gap),
                       career_gap_years=career_gap, note=note)


def match_detail(info, *, experience_text: str = "",
                 level_of: LevelOf | None = None) -> MatchResult:
    """`worknet_detail.DetailInfo` → MatchResult (요건 추출까지 한 번에)."""
    from src.requirements import from_detail
    return match(from_detail(info), experience_text=experience_text, level_of=level_of)


def report(result: MatchResult) -> str:
    """사람이 읽는 갭 리포트. 노션 페이지 본문·슬랙 요약의 재료.

    ⚠️ **마크다운 표를 쓰지 않는다.** `md_to_notion.py`가 표를 파싱하지 못한다
    (원본 CLAUDE.md §Controlled markdown subset).
    """
    lines = [f"**{result.summary()}**", ""]
    if result.fit_score is None:
        lines.append(f"> {result.note}")
        return "\n".join(lines)
    if result.note:
        lines.append(f"> ⚠️ {result.note}")
        lines.append("")

    if result.career_gap_years:
        lines.append(f"- ⛔ 경력 {result.career_gap_years}년 초과 요구")
    for m in result.blocking:
        lines.append(f"- 🔴 {m.label()}")
    for m in result.to_deepen:
        lines.append(f"- 🟡 {m.label()}")
    for m in result.nice_to_have:
        lines.append(f"- ⚪ {m.label()}")
    met = result.by_state(MET)
    if met:
        lines.append("")
        lines.append("충족: " + ", ".join(m.requirement.skill for m in met))
    return "\n".join(lines)
