"""여러 소스를 직무 키워드별로 실행 + 정규화 + 실행 내 중복 제거.

한 공고가 여러 키워드/직무에 걸리는 것은 정상이다. 같은 source_key는 **처음 매칭된
직무**로 확정한다(우선 직무부터 돌기 때문에 cloud/public_it이 우선권을 갖는다).
"""
from __future__ import annotations

from config import roles, settings
from src.sources.base import JobPosting, Source, SourceError, SourceUnreachable


class CollectError(RuntimeError):
    """수집이 전부 실패했다. `unreachable`이면 원인이 **연결**이지 페이지 구조가 아니다.

    상위 안내 문구가 이 둘을 섞으면 `--probe`(구조 점검)로 헛다리를 짚게 된다.
    """

    def __init__(self, message: str, *, unreachable: bool = False):
        super().__init__(message)
        self.unreachable = unreachable


def default_sources() -> list[Source]:
    from src.sources.saramin import SaraminSource
    from src.sources.worknet import WorknetSource
    return [SaraminSource(), WorknetSource()]


def collect(sources: list[Source] | None = None,
            role_slugs: list[str] | None = None,
            *, max_per_role: int | None = None,
            per_keyword: int | None = None,
            log=print) -> list[JobPosting]:
    """직무 × 키워드 × 소스를 돌려 JobPosting 리스트 반환.

    개별 호출 실패는 경고 후 진행(한 소스가 죽어도 나머지는 수집). 단 **모든 호출이
    실패하면** CollectError를 올려 상위가 error 페이지를 남기게 한다.
    """
    sources = sources if sources is not None else default_sources()
    slugs = role_slugs or sorted(roles.SLUGS, key=roles.sort_key)
    max_per_role = max_per_role or settings.MAX_JOBS_PER_ROLE
    per_keyword = per_keyword or settings.PER_KEYWORD_FETCH

    seen: dict[str, JobPosting] = {}
    attempts = failures = unreached = 0
    errors: list[str] = []
    disabled: set[str] = set()   # 키 미설정 등 재시도해도 같은 결과인 소스
    # 소스별 **연속** 연결 실패 횟수. 성공하면 0으로 되돌린다 — 드문드문 나는 실패와
    # IP가 통째로 막힌 상황을 가르는 게 '연속'이라는 조건이다.
    streak: dict[str, int] = {}

    for slug in slugs:
        taken = 0
        for keyword in roles.keywords(slug):
            if taken >= max_per_role:
                break
            for src in sources:
                if taken >= max_per_role:
                    break
                if src.name in disabled:
                    continue
                attempts += 1
                try:
                    found = src.fetch(keyword, slug, per_keyword)
                except SourceError as e:
                    failures += 1
                    errors.append(f"{src.name}/{slug}/{keyword}: {e}")
                    log(f"  [경고] {src.name} '{keyword}' 수집 실패: {e}")
                    if "미설정" in str(e):
                        # 키가 없으면 남은 수십 번의 호출도 전부 같은 실패다 → 이 소스는 접는다.
                        disabled.add(src.name)
                        log(f"  [중단] {src.name} 소스 비활성화 — 이후 키워드는 건너뜀")
                    elif isinstance(e, SourceUnreachable):
                        unreached += 1
                        streak[src.name] = n = streak.get(src.name, 0) + 1
                        if n >= settings.SOURCE_UNREACHABLE_LIMIT:
                            # 키 미설정과 같은 논리다. IP가 막혔다면 남은 호출도 확실히
                            # 같은 결과인데, 타임아웃 하나가 수십 초라 러너의 20분
                            # 예산이 먼저 죽는다.
                            disabled.add(src.name)
                            log(f"  [중단] {src.name} 연속 {n}회 연결 실패 — 이번 실행에서"
                                " 비활성화. 서버에 닿지 못한 것이므로 페이지 구조 문제가"
                                " 아닙니다")
                    continue
                except Exception as e:  # noqa: BLE001 — 한 소스 장애가 전체를 막지 않음
                    failures += 1
                    errors.append(f"{src.name}/{slug}/{keyword}: {type(e).__name__}: {e}")
                    log(f"  [경고] {src.name} '{keyword}' 예외: {type(e).__name__}: {e}")
                    continue
                streak[src.name] = 0   # 한 번이라도 닿았으면 연속이 끊긴다
                for job in found:
                    if job.source_key in seen:
                        continue  # 실행 내 중복 — 먼저 매칭된 직무를 유지
                    seen[job.source_key] = job
                    taken += 1
                    if taken >= max_per_role:
                        break
        log(f"  · {roles.display_name(slug):<16} 수집 {taken}건")

    for name in sorted(disabled):
        log(f"  · {name} 은(는) 이번 실행에서 수집되지 않았습니다")

    if attempts and failures == attempts:
        raise CollectError("모든 소스 호출 실패:\n" + "\n".join(errors[:5]),
                           unreachable=(unreached == failures))
    return list(seen.values())


def sort_for_output(jobs: list[JobPosting]) -> list[JobPosting]:
    """우선 직무 → 회사명 순. 슬랙/노션 노출 순서용."""
    return sorted(jobs, key=lambda j: (roles.sort_key(j.role), j.company, j.title))
