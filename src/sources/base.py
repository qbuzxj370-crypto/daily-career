"""JobPosting(내부 정규화 형식) + Source 프로토콜 + 소스 공용 헬퍼.

각 소스는 원본 응답을 이 형식으로만 내보낸다. 이후 단계(signals/evaluator/notion)는
원본 스키마를 모른다 — 소스가 추가돼도 파이프라인은 그대로다.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol


@dataclass
class JobPosting:
    source: str              # "saramin" | "worknet"
    source_id: str           # 공고 고유 ID
    title: str
    company: str
    url: str
    role: str                # 매칭된 직무 slug
    location: str | None = None
    experience: str | None = None      # "신입", "경력 1~3년" 등
    employment_type: str | None = None
    deadline: str | None = None        # YYYY-MM-DD
    raw_text: str = ""                 # 판정에 넣을 텍스트(제목+업종+근무형태+키워드 합본)
    extra: dict[str, Any] = field(default_factory=dict)  # 소스별 부가정보(디버깅용)

    @property
    def source_key(self) -> str:
        return f"{self.source}:{self.source_id}"

    def to_dict(self) -> dict[str, Any]:
        d = {
            "source": self.source, "source_id": self.source_id, "source_key": self.source_key,
            "title": self.title, "company": self.company, "url": self.url, "role": self.role,
            "location": self.location, "experience": self.experience,
            "employment_type": self.employment_type, "deadline": self.deadline,
            "raw_text": self.raw_text,
        }
        return d


class Source(Protocol):
    """소스 클라이언트 계약. collector는 이 두 메서드만 안다."""
    name: str

    def fetch(self, keyword: str, role: str, limit: int) -> list[JobPosting]: ...

    def probe(self, keyword: str) -> tuple[str, str]:
        """(확장자, 원본 응답 본문) — P1에서 파일로 덤프해 필드 매핑을 확정한다."""
        ...


class SourceError(RuntimeError):
    pass


# ---------------------------------------------------------------- 공용 헬퍼

def get_path(obj: Any, path: str) -> Any:
    """'position.title' 같은 점 경로로 중첩 dict 접근. 없으면 None."""
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
        if cur is None:
            return None
    return cur


def first_str(obj: Any, *paths: str) -> str:
    """후보 경로를 순서대로 시도해 처음 나오는 비어있지 않은 문자열 반환.

    P1 전에는 필드 경로가 확정이 아니므로 후보를 여러 개 두고 실제 응답에 맞는 것이
    걸리게 한다. P1 덤프로 확정되면 후보를 하나로 줄여도 된다.
    """
    for p in paths:
        v = get_path(obj, p)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            v = str(v)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


_WS = re.compile(r"[ \t]+")


def clean(text: str) -> str:
    return _WS.sub(" ", (text or "").replace("\r", " ").replace("\xa0", " ")).strip()


def norm_date(value: str | int | None) -> str | None:
    """마감일을 YYYY-MM-DD로 정규화. 판독 불가면 None(노션 date 속성 생략).

    관측되는 형태: '2026-01-01 23:59:59'(사람인), '20260101'(워크넷), '2026.01.01',
    epoch seconds(문자열/정수).
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    # epoch seconds (10자리 숫자)
    if s.isdigit() and len(s) == 10:
        try:
            return datetime.fromtimestamp(int(s), tz=timezone.utc).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            return None
    if s.isdigit() and len(s) == 8:  # 20260101
        s = f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    m = re.match(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})", s)
    if not m:
        return None
    y, mo, d = (int(g) for g in m.groups())
    try:
        return datetime(y, mo, d).strftime("%Y-%m-%d")
    except ValueError:
        return None


def http_get(url: str, params: dict[str, Any], *, timeout: float,
             headers: dict[str, str] | None = None) -> tuple[str, str]:
    """GET 후 (content-type, 본문 텍스트). 4xx/5xx는 SourceError로 승격."""
    import httpx  # 지연 import: --mock 경로에서는 미설치여도 동작
    try:
        resp = httpx.get(url, params=params, timeout=timeout,
                         headers=headers or {"Accept": "application/json"},
                         follow_redirects=True)
    except Exception as e:  # noqa: BLE001
        raise SourceError(f"HTTP 요청 실패 {url}: {type(e).__name__}: {e}") from e
    if resp.status_code != 200:
        raise SourceError(f"HTTP {resp.status_code} {url}: {resp.text[:300]}")
    return resp.headers.get("content-type", ""), resp.text


def build_raw_text(*parts: str | None) -> str:
    """판정용 합본 텍스트. 빈 조각 제거 후 ' | '로 잇는다."""
    return " | ".join(clean(p) for p in parts if p and clean(p))
