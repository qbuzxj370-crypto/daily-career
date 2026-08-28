"""워크넷/고용24 채용정보 수집기 — **공개 웹 검색 페이지 파싱**.

  GET https://www.work24.go.kr/wk/a/b/1200/retriveDtlEmpSrchList.do
      ?srcKeyword=클라우드&searchMode=Y&resultCnt=30&currentPageNo=1
      &sortField=DATE&sortOrderBy=DESC&siteClcd=all
      &careerTypes=N,Z&academicGbn=00,04&regDateStdt=20260804&regDateEndt=20260810

⚠️ **왜 API가 아니라 스크래핑인가 (2026-08-10 결정, `docs/ref.md` §7(1) 참조)**
고용24 OPEN-API는 소개 페이지에 "기업회원 전용 서비스"라고 명시돼 있고, 실제 호출하면
`개인회원은 사용할 수 없는 OPEN-API입니다`를 돌려준다. data.go.kr도 이 API는 대행하지 않고
고용24로 위임한다. 개인 계정으로 워크넷 데이터를 받을 API 경로가 존재하지 않아, 사용자의
결정으로 공개 검색 페이지 파싱으로 전환했다.

**대상 경로는 robots.txt가 허용한다** (`Allow: /`, 차단 목록은 `/cm/common/`, `/sa/`, `/ei/`,
`/cm/f/c/0100/selectUnifySearchPost.do`). 통합검색(`selectUnifySearchPost.do`)은 명시적으로
금지된 경로이므로 **절대 쓰지 말 것.** 여기서 쓰는 `/wk/a/b/1200/...`는 사이트맵에도 등재돼 있다.

핵심 파라미터: **`searchMode=Y`가 없으면 키워드가 무시되고** 항상 최신순 전체 목록이 온다.
이건 브라우저 실제 요청(DevTools cURL)으로 확정한 값이다 — 문서가 아니라 관측 결과다.

**필터(2026-08-10 실측)** — 값의 출처는 검색 페이지의 체크박스/버튼 value이고,
효과는 `srcKeyword=클라우드` 기준 totalRecordCount 변화로 확인했다(614 → 20):

  careerTypes   경력   N=신입 · E=경력 · Z=관계없음        · 체크박스 name(`careerType`)이
                       아니라 **`careerTypes`**로 보내야 한다(name으로 보내면 무시됨)
  academicGbn   학력   00=학력무관 · 03=고졸 · 04=대졸(2~3년) · 05=대졸(4년) · 06=석사 · 07=박사
  regDateStdt/  등록일 **YYYYMMDD**. 하이픈(`2026-08-04`)을 넣으면 목록이 0건으로 비어버린다.
  regDateEndt          화면의 `termSearchGbn=W-1`은 버튼 상태값일 뿐 서버는 무시한다 —
                       날짜 두 개를 직접 계산해서 줘야 실제로 걸린다.

'1주 이내' 버튼의 JS 계산식은 `오늘-7+1`이므로 시작일 = 오늘-6일이다. 금요일에 돌리면
지난 토요일~오늘이 되어 주 단위로 빈틈 없이 이어진다.

결과는 서버사이드 렌더링이라 JS 실행이 필요 없다. 각 행의 체크박스 value에
`공고번호|정보구분|회사명|공고제목`이 통째로 들어 있어 이걸 1차 앵커로 쓴다.
"""
from __future__ import annotations

import html as _html
import re
from datetime import date, datetime, timedelta
from typing import Any

from config import settings
from src.sources.base import (
    JobPosting, SourceError, build_raw_text, clean, http_get, norm_date,
)

NAME = "worknet"
BASE = "https://www.work24.go.kr"

# 목록 페이지가 브라우저처럼 응답하도록 UA를 명시한다(봇 차단 회피가 아니라 정상 렌더 유도).
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36")

# --- 파싱 앵커 (2026-08-10 실제 응답 기준) ---------------------------------
# 행 분할: <tr id="list1"> ... </tr>
ROW_RE = re.compile(r'<tr[^>]+id="list\d+"[^>]*>(.*?)</tr>', re.S)
# 공고번호|정보구분|회사명|공고제목
CHK_RE = re.compile(r'id="chkboxWantedAuthNo\d+"[^>]*value="([^"]*)"')
DETAIL_RE = re.compile(r'href="(/wk/a/b/1500/empDetailAuthView\.do\?[^"]+)"')
# 마감일은 두 곳에 나온다: D-day 계산용 스크립트 변수와 본문 텍스트. 둘 다 본다.
DEADLINE_RE = re.compile(r"var\s+date\s*=\s*'(\d{4}-\d{2}-\d{2})'")
DEADLINE_TEXT_RE = re.compile(r"마감일\s*:\s*(\d{4}-\d{2}-\d{2})")
# 총 건수는 페이지마다 위치가 다르다: 페이징 스크립트의 `totalRecordCount : 614`가 항상
# 있고, 히든 input 형태는 일부 화면에만 있다. 둘 다 본다(필터 효과 확인용 계측값).
TOTAL_RE = re.compile(r'totalRecordCount\s*:\s*(\d+)')
TOTAL_INPUT_RE = re.compile(r'name="totalRecordCount"[^>]*value="(\d+)"')
LI_RE = {
    "salary": re.compile(r'<li class="dollar">(.*?)</li>', re.S),
    "member": re.compile(r'<li class="member">(.*?)</li>', re.S),   # 경력 · 학력
    "time": re.compile(r'<li class="time">(.*?)</li>', re.S),       # 주5일 · 주 40시간
    "site": re.compile(r'<li class="site">(.*?)</li>', re.S),       # 근무지
}
TAG_RE = re.compile(r"<[^>]+>")


def _text(fragment: str) -> str:
    """HTML 조각 → 표시 텍스트. 태그 제거 + 엔티티 복원 + 공백 정리.

    HTML은 줄바꿈으로 들여쓰기돼 있어 `clean()`(스페이스·탭만 처리)만으로는 부족하다.
    개행까지 한 칸으로 접어야 '연봉 3,500 만원 이상'이 한 줄로 나온다.
    """
    text = _html.unescape(TAG_RE.sub(" ", fragment or ""))
    return clean(re.sub(r"\s+", " ", text))


def _li(row: str, key: str) -> str:
    m = LI_RE[key].search(row)
    return _text(m.group(1)) if m else ""


# 요청 간격은 **프로세스 전역**으로 잰다. collector가 소스 인스턴스를 하나만 만들긴
# 하지만, 간격의 목적이 "이 IP가 work24에 보내는 속도"를 늦추는 것이라 인스턴스가 몇
# 개인지와 무관해야 한다.
_last_request = 0.0


def _throttle(sleep=None) -> float:
    """직전 요청으로부터 `WORKNET_REQUEST_GAP`초가 지나도록 기다린다. 잔 시간을 반환.

    목록 응답이 건당 ~500KB인데 키워드 27개를 지연 없이 연속으로 보내면 버스트로
    보인다(2026-08-28 CI ConnectTimeout의 후보 원인). 국내에서 재보니 총 지연은
    30초 미만이라 주간 배치에서는 무해하다.
    """
    global _last_request
    import time
    sleep = sleep or time.sleep
    gap = settings.WORKNET_REQUEST_GAP
    now = time.monotonic()
    wait = 0.0
    if gap > 0 and _last_request:
        wait = max(0.0, gap - (now - _last_request))
        if wait:
            sleep(wait)
    _last_request = time.monotonic()
    return wait


class WorknetSource:
    """워크넷 공개 검색 결과 수집기. `Source` 프로토콜 구현."""

    name = NAME

    def __init__(self, url: str | None = None, timeout: float | None = None,
                 today: date | None = None):
        self.url = url or settings.WORKNET_SEARCH_URL
        self.timeout = timeout if timeout is not None else settings.HTTP_TIMEOUT
        # 등록일 창의 기준일. 주입 가능하게 둬서 파라미터 조립을 테스트할 수 있게 한다.
        self.today = today

    # ---------------------------------------------------------------- HTTP
    def reg_window(self) -> tuple[str, str]:
        """등록일 필터 구간 (시작, 끝) — YYYYMMDD. **하이픈을 넣으면 0건이 된다.**

        화면의 '1주 이내' 버튼과 같은 계산(`오늘-7+1` = 오늘-6일)을 쓴다.
        """
        end = self.today or datetime.now(settings.TIMEZONE).date()
        start = end - timedelta(days=max(settings.WORKNET_REG_DAYS - 1, 0))
        return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")

    def _params(self, keyword: str, count: int, page: int = 1) -> dict[str, Any]:
        start, end = self.reg_window()
        params: dict[str, Any] = {
            "srcKeyword": keyword,
            "searchMode": "Y",      # ← 이게 없으면 키워드가 무시된다
            "resultCnt": count,
            "currentPageNo": page,
            "pageIndex": page,
            "sortField": "DATE",    # 최신순
            "sortOrderBy": "DESC",
            "siteClcd": "all",
            # 등록일: 최근 WORKNET_REG_DAYS일. 항상 건다(주간 도구라 오래된 공고는 무의미).
            "regDateStdt": start,
            "regDateEndt": end,
        }
        # 빈 문자열로 두면 '필터 없음'이 되므로, 설정이 비어 있으면 아예 보내지 않는다.
        if settings.WORKNET_CAREER_TYPES:
            params["careerTypes"] = settings.WORKNET_CAREER_TYPES
        if settings.WORKNET_ACADEMIC_GBN:
            params["academicGbn"] = settings.WORKNET_ACADEMIC_GBN
        return params

    def _raw(self, keyword: str, count: int) -> str:
        _throttle()
        _ctype, body = http_get(
            self.url, self._params(keyword, count), timeout=self.timeout,
            headers={"User-Agent": USER_AGENT,
                     "Accept": "text/html,application/xhtml+xml"},
        )
        return body

    def probe(self, keyword: str) -> tuple[str, str]:
        return "html", self._raw(keyword, count=10)

    # ---------------------------------------------------------------- 파싱
    def fetch(self, keyword: str, role: str, limit: int) -> list[JobPosting]:
        return self.parse(self._raw(keyword, count=limit), role)[:limit]

    @staticmethod
    def total_count(body: str) -> int | None:
        m = TOTAL_RE.search(body or "") or TOTAL_INPUT_RE.search(body or "")
        return int(m.group(1)) if m else None

    @staticmethod
    def rows(body: str) -> list[str]:
        return ROW_RE.findall(body or "")

    @classmethod
    def parse(cls, body: str, role: str) -> list[JobPosting]:
        rows = cls.rows(body)
        if not rows:
            # 결과 0건과 '레이아웃이 바뀌어 파싱 실패'를 구분한다. 후자를 조용히 넘기면
            # 매주 0건이 되고도 이유를 알 수 없다.
            #
            # 판별은 **총건수**로 한다. `chkboxWantedAuthNo` 문자열의 유무로는 못 가른다 —
            # 0건 페이지에도 그 id를 참조하는 JS가 그대로 실려 온다(2026-08-10 확인).
            # 필터를 걸기 전에는 어떤 키워드든 결과가 있어서 이 오판이 드러나지 않았다.
            total = cls.total_count(body)
            if total:
                raise SourceError(
                    f"워크넷 목록 행을 분할하지 못했습니다(총 {total}건인데 행 0개) — 페이지"
                    " 구조가 바뀐 것으로 보입니다 (`--probe worknet`으로 덤프 확인 필요)")
            if total is None:
                raise SourceError(
                    "워크넷 응답에서 총건수를 찾지 못했습니다 — 검색 결과 페이지가 아니거나"
                    " 구조가 바뀐 것으로 보입니다 (`--probe worknet`으로 덤프 확인 필요)")
            return []   # total == 0: 필터 조건에 맞는 공고가 정말로 없는 주다
        out: list[JobPosting] = []
        for row in rows:
            job = cls._to_posting(row, role)
            if job is not None:
                out.append(job)
        return out

    @classmethod
    def _to_posting(cls, row: str, role: str) -> JobPosting | None:
        m = CHK_RE.search(row)
        if not m:
            return None
        parts = _html.unescape(m.group(1)).split("|")
        if len(parts) < 4:
            return None
        source_id, info_type, company, title = (p.strip() for p in parts[:4])
        if not source_id or not title:
            return None

        dm = DETAIL_RE.search(row)
        url = BASE + _html.unescape(dm.group(1)) if dm else ""

        salary = _li(row, "salary")
        member = _li(row, "member")      # "경력무관 학력무관"
        work_time = _li(row, "time")     # "주5일 주 40시간 근로"
        location = _li(row, "site")
        dl = DEADLINE_RE.search(row) or DEADLINE_TEXT_RE.search(_html.unescape(row))

        return JobPosting(
            source=NAME,
            source_id=source_id,
            title=clean(title),
            company=clean(company),
            url=url,
            role=role,
            location=location or None,
            experience=member or None,
            employment_type=work_time or None,
            deadline=norm_date(dl.group(1)) if dl else None,
            # 근무형태(주5일/교대 여부)가 여기 실려 오므로 판정 텍스트에 반드시 포함한다.
            raw_text=build_raw_text(title, company, work_time, member, salary, location),
            extra={"info_type": info_type, "salary": salary, "work_time": work_time},
        )
