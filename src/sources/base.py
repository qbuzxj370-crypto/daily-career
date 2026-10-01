"""JobPosting(내부 정규화 형식) + Source 프로토콜 + 소스 공용 헬퍼.

각 소스는 원본 응답을 이 형식으로만 내보낸다. 이후 단계(signals/evaluator/notion)는
원본 스키마를 모른다 — 소스가 추가돼도 파이프라인은 그대로다.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from config import settings


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


class SourceUnreachable(SourceError):
    """서버에 **닿지도 못한** 실패 — 연결 타임아웃·DNS·전송 계층 오류.

    파싱 실패(`SourceError`)와 반드시 구분한다. 파싱 실패는 200을 받고 내용이 달라진
    것이니 `--probe`로 구조를 봐야 하고, 이건 응답 자체가 없으니 구조를 봐도 아무것도
    안 나온다. 2026-08-28 CI 실행에서 워크넷이 `ConnectTimeout`으로 죽었는데 안내 문구가
    둘을 섞어 놔서 페이지 구조 변경을 먼저 의심했다 — 실제 페이지는 멀쩡했다.
    """


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


# 다시 보내면 달라질 수 있는 상태코드만 재시도한다. 4xx(인증·잘못된 파라미터)는
# 몇 번을 보내도 같은 답이므로 재시도하면 시간만 버린다.
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})

# 검색어를 로그에 같이 싣기 위한 후보 파라미터명(소스마다 다르다).
_KEYWORD_PARAMS = ("srcKeyword", "keywords", "keyword")


def _default_log(message: str) -> None:
    """재시도 로그의 기본 출력. stderr로 보내 CI 스텝 로그에 그대로 남긴다."""
    import sys
    print(message, file=sys.stderr, flush=True)


def _describe(url: str, params: dict[str, Any]) -> str:
    """로그 한 줄에 들어갈 요청 식별자. 호스트 + 검색어면 충분하다."""
    from urllib.parse import urlsplit
    host = urlsplit(url).netloc or url
    for key in _KEYWORD_PARAMS:
        value = params.get(key) if params else None
        if value:
            return f"{host} '{value}'"
    return host


def http_get(url: str, params: dict[str, Any], *, timeout: float,
             headers: dict[str, str] | None = None,
             retries: int | None = None, backoff: float | None = None,
             sleep=None, log=None) -> tuple[str, str]:
    """GET 후 (content-type, 본문 텍스트).

    - 전송 계층 실패(연결 타임아웃·DNS·리셋)는 백오프 후 재시도하고, 끝내 실패하면
      **`SourceUnreachable`** 로 올린다(구조 문제와 구분하기 위한 별도 타입).
    - 429/5xx도 재시도한다. 그 외 4xx/5xx는 즉시 `SourceError`.
    - 백오프는 지수(`backoff * 2**n`) + 지터. 지터는 27개 키워드가 같은 리듬으로
      동시에 재시도해 두 번째 버스트를 만드는 것을 막는다.
    - `sleep`은 테스트에서 실제로 안 자게 주입하는 자리다.

    **재시도는 반드시 로그를 남긴다.** 조용히 성공하면 나중에 "재시도가 살린 것"과
    "원래 문제가 없던 것"을 구분할 수 없다. 2026-08-28에 실제로 이 구분이 안 돼서
    실행 하나를 통째로 추측으로 해석해야 했다 — 성공한 재시도는 아무 흔적도 안 남겼다.
    """
    import random
    import time

    import httpx  # 지연 import: --mock 경로에서는 미설치여도 동작

    retries = settings.HTTP_RETRIES if retries is None else retries
    backoff = settings.HTTP_BACKOFF if backoff is None else backoff
    sleep = sleep or time.sleep
    log = log or _default_log
    # 연결 수립만 짧게 끊는다 — 방화벽이 SYN을 버리는 경우 전체 타임아웃까지 서 있을
    # 이유가 없다. 읽기/쓰기는 목록 HTML이 ~500KB라 넉넉히 둔다.
    limits = httpx.Timeout(timeout, connect=min(settings.HTTP_CONNECT_TIMEOUT, timeout))

    # ⚠️ httpx는 params가 주어지면 URL의 쿼리를 **replace**한다. 쿼리가 든 URL에
    # 빈 params를 넘기면 파라미터가 조용히 전부 지워진다(2026-10-01 실제 사고).
    # 호출자가 URL에 쿼리를 담아 보냈고 params가 비어 있으면 건드리지 않는다.
    if not params and "?" in url:
        params = None

    last = ""
    reached = False   # 한 번이라도 응답 헤더를 받았는가 (429/5xx는 '닿았지만 거절')
    for attempt in range(retries + 1):
        if attempt:
            wait = backoff * (2 ** (attempt - 1)) * (1 + random.random() * 0.25)
            log(f"  [재시도] {_describe(url, params)} {attempt}/{retries}회 — "
                f"{last} (다음 시도까지 {wait:.1f}s)")
            sleep(wait)
        try:
            resp = httpx.get(url, params=params, timeout=limits,
                             headers=headers or {"Accept": "application/json"},
                             follow_redirects=True)
        except httpx.TransportError as e:
            # 연결/타임아웃/리셋 — 응답이 없다. 재시도 대상.
            last = f"{type(e).__name__}: {e}"
            continue
        except Exception as e:  # noqa: BLE001 — 그 외는 코드/설정 문제이므로 재시도 무의미
            raise SourceError(f"HTTP 요청 실패 {url}: {type(e).__name__}: {e}") from e
        if resp.status_code in RETRY_STATUS:
            last = f"HTTP {resp.status_code}"
            reached = True
            continue
        if resp.status_code != 200:
            raise SourceError(f"HTTP {resp.status_code} {url}: {resp.text[:300]}")
        if attempt:
            # 이 줄이 "재시도가 살렸다"의 유일한 증거다. 없으면 성공한 실행에서
            # 일시 장애가 있었는지조차 알 수 없다.
            log(f"  [복구] {_describe(url, params)} — {attempt}회 재시도 후 성공")
        return resp.headers.get("content-type", ""), resp.text

    tries = retries + 1
    if reached:
        # 서버는 살아 있고 거절만 한 것 — 차단/과부하이지 연결 문제가 아니다.
        raise SourceError(f"{tries}회 시도 모두 거절됨 {url} (마지막: {last})")
    raise SourceUnreachable(
        f"응답을 받지 못했습니다 {url}: {tries}회 시도 모두 실패 (마지막: {last}). "
        "서버에 닿지 못한 것이라 페이지 구조와는 무관합니다")


def build_raw_text(*parts: str | None) -> str:
    """판정용 합본 텍스트. 빈 조각 제거 후 ' | '로 잇는다."""
    return " | ".join(clean(p) for p in parts if p and clean(p))
