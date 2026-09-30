"""내 스펙 정본 — 판정의 **두 번째 입력**.

원본 career-scout의 판정은 입력이 하나였다(공고를 고정된 기준에 비춘다). 이 프로젝트는
`공고 × 내 스펙 → (적합도, 부족한 것)`이므로 스펙이 1급 입력이고, 이 파일이 그 정본이다.
`config/rules.py`가 "기피 조건"의 정본인 것과 같은 지위다.

⚠️ **숙련도는 보수적으로 적을 것.** 레벨을 실제보다 높게 적으면 갭이 숨는다 — 도구가
"당신은 준비됐다"고 거짓말하게 되고, 그러면 존재 이유가 사라진다. 애매하면 낮은 쪽을
고른다. 낮게 적어서 생기는 손해는 "이미 아는 걸 또 공부하라고 한다" 정도이고, 높게
적어서 생기는 손해는 **못 뚫는 이유를 영원히 모르는 것**이다.

⚠️ **자격증은 스킬 레벨을 채우지 않는다.** SQLD를 가졌어도 `SQL` 레벨은 0일 수 있다.
매처가 그 경우를 `부분 충족`으로 따로 표시한다(`config/skills.py` §CERT_EVIDENCE).
"""
from __future__ import annotations

# --- 숙련도 등급 ------------------------------------------------------------
# 공고의 `요구 깊이`(src/requirements.py의 DEPTH_*)와 같은 척도로 비교된다.
LEVEL_NONE = 0       # 없음
LEVEL_BASIC = 1      # 문법·기본 개념을 안다 (강의/독학 수준)
LEVEL_PRACTICE = 2   # 직접 만들어 봤다 (과제·토이·팀 프로젝트)
LEVEL_WORK = 3       # 실무 수준으로 쓸 수 있다 (운영·설계 경험)

LEVEL_NAMES = {
    LEVEL_NONE: "없음",
    LEVEL_BASIC: "기본",
    LEVEL_PRACTICE: "실습",
    LEVEL_WORK: "실무",
}

# --- 목표 직군 (우선순위 순) -------------------------------------------------
# 1순위는 백엔드. 다만 조건에 따라 인접 직군으로도 간다는 전제이므로, 목록에서 지우지
# 않고 우선순위로만 조절한다(원본 `roles.py`의 "slug를 삭제하지 말 것"과 같은 이유 —
# 삭제하면 노션 select 옵션과 기발행 페이지가 무효화된다).
TARGET_ROLES: tuple[str, ...] = (
    "backend",       # ★1목표
    "web_dev",       # SI·웹개발 (신입 채용이 가장 많은 경로)
    "public_it",     # 공공·기관 전산직 — 정처산기·SQLD·리눅스마스터가 직접 가점되는 트랙
    "fullstack",     # 리액트 기본이 있어 열려 있는 경로
)

# --- 보유 스킬 -> 숙련도 ----------------------------------------------------
# 키는 `config/skills.py`의 **정본 이름**이어야 한다. 오타는 조용히 무시되지 않고
# `tests/test_profile.py::test_every_profile_skill_is_canonical`이 잡는다.
SKILL_LEVELS: dict[str, int] = {
    # 자바 계열 — 클래스·상속·스프링부트까지. 강의·과제 수준이라 실습으로 둔다.
    "Java": LEVEL_PRACTICE,
    "Spring Boot": LEVEL_PRACTICE,
    "Spring": LEVEL_PRACTICE,
    "객체지향": LEVEL_PRACTICE,      # 클래스·상속을 직접 써 봤으므로

    # C — 포인터·구조체까지. 언어 기초는 있으나 프로젝트 산출물 기준은 아님.
    "C": LEVEL_BASIC,

    # 파이썬 — 본인이 '기본'이라고 명시.
    "Python": LEVEL_BASIC,

    # 리액트 — 훅·Vite까지 만져 봤으나 '기본'이라고 명시.
    "React": LEVEL_BASIC,
    "JavaScript": LEVEL_BASIC,       # 리액트를 쓰려면 필연

    # ⬇️ 자격증만 있고 실물 경험은 미확인인 것들. **0으로 둔다.**
    #    SQLD·리눅스마스터·AWS AIF가 CERT_EVIDENCE로 '부분 충족'을 만들어 준다.
    #    실제로 써 봤다면 여기 레벨을 올릴 것 — 그게 갭 목록을 바로 줄인다.
    "SQL": LEVEL_NONE,
    "Linux": LEVEL_NONE,
    "AWS": LEVEL_NONE,
}

# --- 보유 자격증 ------------------------------------------------------------
# 키는 `config/skills.py`의 CERTS 정본 이름.
CERTS: tuple[str, ...] = (
    "정보처리산업기사",
    "SQLD",
    "리눅스마스터 2급",
    "AWS AIF",
)

# --- 학력 · 경력 ------------------------------------------------------------
# 워크넷 검색 필터(`WORKNET_ACADEMIC_GBN` / `WORKNET_CAREER_TYPES`)와 같은 축이다.
EDUCATION = "대졸(2~3년)"    # 전문대 — 워크넷 코드 04
CAREER = "신입"               # 워크넷 코드 N
MAX_REQUIRED_YEARS = 1        # 요구 경력이 이 값을 넘으면 매처가 경력 갭으로 표시


def level(skill: str) -> int:
    """보유 숙련도. 목록에 없으면 0(없음)."""
    return SKILL_LEVELS.get(skill, LEVEL_NONE)


def has_cert(cert: str) -> bool:
    return cert in CERTS


def owned_skills(min_level: int = LEVEL_BASIC) -> list[str]:
    """`min_level` 이상으로 보유한 스킬. 기본값은 '기본 이상'."""
    return [s for s, lv in SKILL_LEVELS.items() if lv >= min_level]
