"""검색 직무 정본. slug = 정본 키, 표시명/키워드는 API 호출·프롬프트·렌더용.

docs/career-plan.md §검색 직무 그대로. **slug를 삭제하지 말 것** — 삭제하면 Notion Role
select 옵션과 기발행 페이지가 무효화된다. 범위를 좁힐 땐 PRIORITY_SLUGS로 노출 순서를
조정하거나 KEYWORDS를 줄인다(= daily-recall의 "가중치로 좁히되 목록은 유지" 패턴).
"""
from __future__ import annotations

# slug -> (표시명, [검색 키워드])
ROLES: dict[str, tuple[str, list[str]]] = {
    "cloud": ("클라우드 엔지니어", [
        "클라우드", "AWS", "MSP", "클라우드엔지니어", "퍼블릭클라우드",
    ]),
    "public_it": ("공공기관 전산직", [
        "전산직", "전산실", "정보화", "공공 전산", "정보시스템 운영",
        # 전산실 운영(공공·병원·대학)은 이 slug에 포함 (docs/career-plan.md §검색 직무 각주)
        "병원 전산", "대학 전산",
    ]),
    "sys_ops": ("시스템 운영/SE", [
        "시스템운영", "시스템엔지니어", "리눅스", "유닉스", "서버운영",
    ]),
    "tech_support": ("기술지원 (TS/TA)", [
        "기술지원", "테크니컬서포트", "TA", "유지보수", "솔루션엔지니어",
    ]),
    "network": ("네트워크 운영/지원", [
        "네트워크운영", "네트워크엔지니어", "스위치", "라우터", "NE",
    ]),
}

# 정본 slug 목록 (Notion Role select 옵션의 원천)
SLUGS: list[str] = list(ROLES.keys())

# 주요 관심 직무. 슬랙 요약·노션 정렬에서 먼저 노출된다.
# (settings.ROLE_PRIORITY로 재노출 — 튜닝 상수 위치는 settings, 정본은 여기)
PRIORITY_SLUGS: tuple[str, ...] = ("cloud", "public_it")


def display_name(slug: str) -> str:
    return ROLES[slug][0]


def keywords(slug: str) -> list[str]:
    return ROLES[slug][1]


def is_priority(slug: str) -> bool:
    return slug in PRIORITY_SLUGS


def sort_key(slug: str) -> tuple[int, int]:
    """우선 직무 먼저, 그다음 정본 선언 순서."""
    pri = PRIORITY_SLUGS.index(slug) if slug in PRIORITY_SLUGS else len(PRIORITY_SLUGS)
    return (pri, SLUGS.index(slug) if slug in SLUGS else len(SLUGS))
