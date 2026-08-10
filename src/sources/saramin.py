"""사람인 오픈 API 클라이언트 (공식 API. 스크래핑 없음).

  GET https://oapi.saramin.co.kr/job-search?access-key=...&keywords=...&start=0&count=N

⚠️ **필드 매핑은 P1 미검증 상태다.** docs/career-plan.md §P1이 요구하는 대로
`--probe saramin --role cloud`로 실제 응답을 덤프해 확인한 뒤 `FIELDS`를 확정하라.
지금은 후보 경로를 여러 개 두고 first_str()이 먼저 걸리는 것을 쓰는 방어적 매핑이다
(문서와 실제 응답이 어긋나는 흔한 케이스를 흡수하기 위함이며, 확정을 대신하지 않는다).

⚠️ **알려진 한계(숨기지 않는다)**: 이 검색 API는 회사 사원수/기업규모를 주지 않는다.
게다가 공고 본문(상세 요강)도 없고 메타데이터만 온다 — 따라서 `3교대`/`관제` 같은
문구 신호는 제목·업종·키워드 안에 드러난 경우에만 잡힌다. "소기업·오너 1인" 판정은
LLM이 `판단 불가`를 반환하고 사람이 노션에서 최종 확인하는 흐름으로 간다.
"""
from __future__ import annotations
import json
from typing import Any

from config import settings
from src.sources.base import (
    JobPosting, SourceError, build_raw_text, clean, first_str, get_path,
    http_get, norm_date,
)

NAME = "saramin"

# 응답 필드 후보 경로 (앞쪽 우선). P1 덤프로 확정하면 후보를 줄일 것.
FIELDS: dict[str, tuple[str, ...]] = {
    "id": ("id", "job-id"),
    "url": ("url", "href"),
    "title": ("position.title", "title"),
    "company": ("company.detail.name", "company.name"),
    "location": ("position.location.name", "position.location"),
    "experience": ("position.experience-level.name", "position.experience-level"),
    "employment_type": ("position.job-type.name", "position.job-type"),
    "deadline": ("expiration-date", "expiration-timestamp", "close-date"),
    "industry": ("position.industry.name", "company.detail.industry.name"),
    "job_category": ("position.job-code.name", "position.job-mid-code.name"),
    "education": ("position.required-education-level.name",),
    "keyword": ("keyword",),
}


class SaraminSource:
    name = NAME

    def __init__(self, access_key: str | None = None, url: str | None = None,
                 timeout: float | None = None):
        self.access_key = access_key if access_key is not None else settings.SARAMIN_ACCESS_KEY
        self.url = url or settings.SARAMIN_API_URL
        self.timeout = timeout if timeout is not None else settings.HTTP_TIMEOUT

    # ---------------------------------------------------------------- HTTP
    def _params(self, keyword: str, count: int, start: int = 0) -> dict[str, Any]:
        return {
            "access-key": self.access_key,
            "keywords": keyword,
            "start": start,
            "count": count,
        }

    def _raw(self, keyword: str, count: int) -> str:
        if not self.access_key:
            raise SourceError("SARAMIN_ACCESS_KEY 미설정 (사람인 오픈API 신청 후 .env에 등록)")
        _ctype, body = http_get(self.url, self._params(keyword, count), timeout=self.timeout)
        return body

    def probe(self, keyword: str) -> tuple[str, str]:
        """P1: 원본 응답 그대로 반환(파일 덤프용)."""
        return "json", self._raw(keyword, count=3)

    # ---------------------------------------------------------------- 파싱
    def fetch(self, keyword: str, role: str, limit: int) -> list[JobPosting]:
        return self.parse(self._raw(keyword, count=limit), role)[:limit]

    @staticmethod
    def items(payload: Any) -> list[dict]:
        """응답에서 공고 배열을 꺼낸다. 단일 객체로 오는 경우도 리스트로 승격."""
        jobs = get_path(payload, "jobs.job")
        if jobs is None:
            jobs = payload.get("job") if isinstance(payload, dict) else None
        if jobs is None:
            return []
        if isinstance(jobs, dict):
            return [jobs]
        return [j for j in jobs if isinstance(j, dict)]

    @classmethod
    def parse(cls, body: str, role: str) -> list[JobPosting]:
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as e:
            raise SourceError(f"사람인 응답 JSON 파싱 실패: {e} / 앞부분={body[:200]!r}") from e
        if isinstance(payload, dict) and payload.get("error"):
            raise SourceError(f"사람인 API 오류: {payload['error']}")
        return [p for p in (cls._to_posting(j, role) for j in cls.items(payload)) if p]

    @classmethod
    def _to_posting(cls, job: dict, role: str) -> JobPosting | None:
        f = FIELDS
        source_id = first_str(job, *f["id"])
        title = clean(first_str(job, *f["title"]))
        if not source_id or not title:
            # id·제목이 없으면 중복 제거 키를 만들 수 없다 → 조용히 버리지 말고 경고.
            print(f"  [경고] saramin: id/title 추출 실패, 1건 건너뜀 (키: {sorted(job)[:8]})")
            return None
        industry = first_str(job, *f["industry"])
        job_category = first_str(job, *f["job_category"])
        keyword_field = first_str(job, *f["keyword"])
        education = first_str(job, *f["education"])
        location = clean(first_str(job, *f["location"])) or None
        experience = clean(first_str(job, *f["experience"])) or None
        emp_type = clean(first_str(job, *f["employment_type"])) or None
        company = clean(first_str(job, *f["company"])) or "(회사명 미상)"
        return JobPosting(
            source=NAME,
            source_id=source_id,
            title=title,
            company=company,
            url=first_str(job, *f["url"]),
            role=role,
            location=location,
            experience=experience,
            employment_type=emp_type,
            deadline=norm_date(first_str(job, *f["deadline"]) or None),
            raw_text=build_raw_text(title, company, industry, job_category,
                                    emp_type, location, experience, education,
                                    keyword_field.replace(",", " · ")),
            extra={"industry": industry, "job_category": job_category},
        )
