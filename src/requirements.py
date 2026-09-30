"""공고 텍스트 → 요건 목록 `{스킬, 필수/우대, 요구 깊이}`.

상세 페이지(`src/sources/worknet_detail.py`)가 준 본문·우대사항을 구조화한다.
**필수/우대 분리와 요구 깊이가 이 모듈의 존재 이유다.**

왜 그 둘인가.
  - **필수/우대 분리** — 공고의 기술 나열은 부풀려져 있다. 담당자가 "있으면 좋고"를 다
    적기 때문이다. `Redis 언급 22%, 그중 필수 3%`에서 두 숫자의 간격이 실제 정보다.
    다행히 워크넷 상세는 `직무내용`과 `우대사항`을 **별도 항목**으로 준다.
  - **요구 깊이** — `Docker 요구 40%`보다 `이 직군은 Docker를 운영 수준으로 요구한다`가
    쓸모 있다. 준비 방향이 아예 달라진다. 같은 단어라도 `Docker 이해`와 `Docker 운영
    경험`은 다른 요구다.

**LLM을 쓰지 않는다.** 사전 매칭과 문구 규칙은 결정적(deterministic)이고, 결정적인 것을
모델에 맡기면 같은 공고가 실행마다 다르게 판정된다. 원본 `CLAUDE.md`의 "규칙이 LLM을
이긴다"와 같은 논리다 — 모델은 요약·설명을 하고, 사실 확정은 코드가 한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from config import skills

# --- 요구 깊이 --------------------------------------------------------------
# `config/profile.py`의 LEVEL_*과 **같은 척도**다. 그래야 매처가 그냥 비교할 수 있다.
DEPTH_UNDERSTAND = 1   # 이해·학습 수준이면 된다
DEPTH_EXPERIENCE = 2   # 써 본 경험을 요구한다  ← 문구 단서가 없을 때의 기본값
DEPTH_PROFICIENT = 3   # 능숙·운영·설계를 요구한다

DEPTH_NAMES = {
    DEPTH_UNDERSTAND: "이해",
    DEPTH_EXPERIENCE: "경험",
    DEPTH_PROFICIENT: "실무",
}

# 깊이 단서. **긴 것부터 검사**하지 않아도 되도록 서로 겹치지 않게 골랐다.
DEPTH_CUES: dict[int, tuple[str, ...]] = {
    DEPTH_PROFICIENT: ("능숙", "숙련", "전문", "운영 경험", "운영경험", "설계",
                       "구축 경험", "구축경험", "최적화", "튜닝", "리팩토링",
                       "장애 대응", "장애대응", "3년", "5년"),
    DEPTH_EXPERIENCE: ("경험", "사용", "활용", "개발 경험", "다룰 수", "가능자",
                       "실무", "프로젝트"),
    DEPTH_UNDERSTAND: ("이해", "학습", "관심", "기초", "지식", "습득", "교육 이수",
                       "배운", "수준"),
}

# 이 문구가 있는 절(clause)의 스킬은 **본문에 있어도 우대**로 본다.
# 워크넷은 `우대사항` 항목을 따로 주지만, 담당자가 본문에 다시 적는 경우가 흔하다.
PREFERRED_CUES: tuple[str, ...] = (
    "우대", "가산", "플러스", "있으면 좋", "선호", "환영", "유리",
)

# 절 분할: 문장부호 + 글머리표 + 개행. 공고 본문은 문장보다 항목 나열이 많다.
CLAUSE_SPLIT = re.compile(r"[.。!?\n·•]|(?<!\d)\s-\s|\d\s*\.\s|、|,\s")

# 요구 경력 연수. `1년 이상`, `2~3년`, `3년以上` 같은 형태.
YEARS_RE = re.compile(r"(\d+)\s*(?:~\s*(\d+)\s*)?년")

KIND_REQUIRED = "필수"
KIND_PREFERRED = "우대"


@dataclass(frozen=True)
class Requirement:
    """요건 1건. `skill`은 `config/skills.py`의 정본 이름이다."""
    skill: str
    kind: str          # KIND_REQUIRED | KIND_PREFERRED
    depth: int         # DEPTH_*
    field: str         # 어느 항목에서 나왔나 (duty / preferred / license / …)
    evidence: str      # 근거가 된 절. **판정을 사람이 검증할 수 있어야 한다**

    @property
    def is_required(self) -> bool:
        return self.kind == KIND_REQUIRED

    def label(self) -> str:
        return f"{self.skill}({DEPTH_NAMES.get(self.depth, '?')}·{self.kind})"


def clauses(text: str) -> list[str]:
    """텍스트를 절 단위로 쪼갠다. 깊이·우대 판단을 **절 안에서만** 하기 위한 것.

    문서 전체를 한 덩어리로 보면 맨 끝의 `우대` 한 단어가 앞의 모든 스킬을 우대로
    바꿔 버린다. 절로 끊으면 그 오염이 절 밖으로 새지 않는다.
    """
    return [c.strip() for c in CLAUSE_SPLIT.split(text or "") if c and c.strip()]


def depth_of(clause: str) -> int:
    """절의 요구 깊이. 단서가 없으면 `DEPTH_EXPERIENCE`.

    기본값을 '경험'으로 두는 이유: 공고가 수식어 없이 `Java`만 적었다면 그건 **써 본 것**을
    기대한다는 뜻이다. '이해'를 기본으로 두면 갭이 실제보다 작게 나오고, '실무'를 기본으로
    두면 모든 공고가 불가능해 보인다.
    """
    for depth in (DEPTH_PROFICIENT, DEPTH_EXPERIENCE, DEPTH_UNDERSTAND):
        if any(cue in clause for cue in DEPTH_CUES[depth]):
            return depth
    return DEPTH_EXPERIENCE


def is_preferred_clause(clause: str) -> bool:
    return any(cue in clause for cue in PREFERRED_CUES)


def required_years(text: str) -> int | None:
    """요구 경력 연수(최솟값). `신입`·`무관`이면 0, 못 읽으면 None.

    범위(`2~3년`)는 **작은 쪽**을 쓴다. 지원 가능성을 판단하는 도구이므로 문턱은 하한이다.
    """
    if not text:
        return None
    if any(word in text for word in ("신입", "무관", "관계없음", "경력무관")):
        return 0
    m = YEARS_RE.search(text)
    if not m:
        return None
    return int(m.group(1))


def _extract(text: str, field: str, *, default_kind: str) -> list[Requirement]:
    out: list[Requirement] = []
    for clause in clauses(text):
        found = skills.find_skills(clause)
        if not found:
            continue
        kind = KIND_PREFERRED if is_preferred_clause(clause) else default_kind
        depth = depth_of(clause)
        evidence = clause if len(clause) <= 120 else clause[:120] + "…"
        for skill in found:
            out.append(Requirement(skill=skill, kind=kind, depth=depth,
                                   field=field, evidence=evidence))
    return out


def _dedupe(items: list[Requirement]) -> list[Requirement]:
    """스킬당 1건으로 줄인다. **필수가 우대를 이기고, 깊은 쪽이 얕은 쪽을 이긴다.**

    같은 스킬이 본문과 우대사항에 모두 있으면 필수로 남겨야 한다 — 우대로 낮추면 갭이
    과소평가되고, 이 도구의 실패 방향은 '갭을 작게 보는 것'이다.
    """
    best: dict[str, Requirement] = {}
    for item in items:
        prev = best.get(item.skill)
        if prev is None:
            best[item.skill] = item
            continue
        # 필수 > 우대, 같은 kind면 깊은 쪽
        if (prev.is_required, prev.depth) < (item.is_required, item.depth):
            best[item.skill] = item
    return [best[s] for s in skills.ALL_SKILLS if s in best]


def from_texts(duty: str = "", preferred: str = "", license_: str = "",
               computer_skill: str = "") -> list[Requirement]:
    """항목별 텍스트에서 요건 목록을 만든다.

    **항목이 kind의 1차 근거다.** `우대사항`·`자격 면허`에서 나온 것은 우대,
    `직무내용`에서 나온 것은 필수가 기본이고, 절 안의 `우대` 문구가 그것을 뒤집는다.
    """
    items: list[Requirement] = []
    items += _extract(duty, "duty", default_kind=KIND_REQUIRED)
    items += _extract(preferred, "preferred", default_kind=KIND_PREFERRED)
    items += _extract(license_, "license", default_kind=KIND_PREFERRED)
    items += _extract(computer_skill, "computer_skill", default_kind=KIND_REQUIRED)
    return _dedupe(items + _expand_implied(items))


def _expand_implied(items: list[Requirement]) -> list[Requirement]:
    """함의된 요건을 추가한다 (`Spring Boot` → `Spring`, `Java`).

    공고가 `Spring Boot 경험자`만 적고 `Java`를 안 적는다고 자바가 필요 없는 게 아니다.
    깊이와 kind는 원 요건을 그대로 물려받되, `evidence`에 함의임을 표시해 사람이 검증할 수
    있게 한다 — 근거 없는 요건이 목록에 끼면 갭 리포트를 신뢰할 수 없게 된다.
    """
    out: list[Requirement] = []
    for item in items:
        for skill in skills.implied(item.skill):
            out.append(Requirement(
                skill=skill, kind=item.kind, depth=item.depth, field=item.field,
                evidence=f"{item.skill} 요구에 포함 ({item.evidence})"))
    return out


def from_detail(info) -> list[Requirement]:
    """`worknet_detail.DetailInfo` → 요건 목록."""
    return from_texts(
        duty=getattr(info, "duty", "") or "",
        preferred=getattr(info, "preferred", "") or "",
        license_=getattr(info, "license", "") or "",
        computer_skill=getattr(info, "computer_skill", "") or "",
    )


def certs_in(info) -> list[str]:
    """공고가 언급한 자격증(정본 이름)."""
    text = " ".join(filter(None, [
        getattr(info, "license", "") or "",
        getattr(info, "preferred", "") or "",
        getattr(info, "duty", "") or "",
    ]))
    return skills.find_certs(text)
