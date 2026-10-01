"""워크넷 공고 **상세 페이지** 파서 — 목록에 없는 본문·근로자수·우대사항을 가져온다.

  GET https://www.work24.go.kr/wk/a/b/1500/empDetailAuthView.do
      ?wantedAuthNo={공고번호}&infoTypeCd=VALIDATION&infoTypeGroup=tb_workinfoworknet

⚠️ **왜 새로 만드는가 — `CLAUDE.md`의 "알려진 한계"는 목록 페이지 얘기였다.**
CLAUDE.md와 `docs/ref.md`는 "두 소스 모두 사원수와 공고 본문을 주지 않는다"를 움직일 수 없는
한계로 기록하고 `Verdict.company_size = 판단 불가`를 그 결론으로 삼았다. 그 판단은
**목록 행(`/wk/a/b/1200/`)과 사람인 검색 API**에 대해서는 맞다. 하지만 **워크넷 상세
페이지는 둘 다 준다.** 실제 공고 2건(2026-09-30 관측):

  K170082609300018 → 근로자수 5명, 직무내용 본문 있음, 우대사항 항목 있음
  K130042609300051 → 근로자수 2명, 직무내용 본문 있음, 우대사항 `전산회계1급, 세무회계(2급)`

즉 `소기업·오너 1인`(사용자의 4대 판정 기준 중 하나)은 **문구에 우연히 드러난 경우만 잡히는
신호가 아니라 숫자로 확정할 수 있는 신호**다. 상세를 한 단계 더 받으면 그 기준이 되살아난다.

**robots.txt** — `/wk/a/b/1500/`은 차단 목록(`/cm/common/`, `/sa/`, `/ei/`,
`/cm/f/c/0100/selectUnifySearchPost.do`)에 없다. 목록 경로와 같은 근거로 허용이다.
사람인은 여전히 `Disallow: /zf_user/recruit/`이므로 **상세도 절대 스크래핑하지 않는다.**

───────────────────────────────────────────────────────────────────────────────
⚠️ **P1: 무엇이 검증됐고 무엇이 아닌지 정확히 구분할 것.**

  검증됨   **라벨 문자열과 값의 존재.** 실제 공고 2건의 렌더된 화면에서 관측했다 —
           `직무내용` `우대사항` `근로자수` `자격 면허` `근무 형태` `주 소정근로시간`
           `전공` `컴퓨터 활용 능력` `지원자격`.
  미검증   **HTML 구조.** 이 라벨들이 `<th>`인지 `<dt>`인지 `<span class="tit">`인지
           확인하지 못했다(작업 환경의 이그레스 정책이 work24를 차단해 원본 HTML을 받지 못함).

그래서 이 파서는 **CSS 클래스나 특정 태그에 앵커를 걸지 않는다.** 대신 **모든 태그를 줄바꿈으로
바꿔** 표시 텍스트를 줄 목록으로 만들고, **한 줄이 라벨과 같으면 그 뒤 줄들을 값으로 본다.**

줄 단위로 끊는 것이 핵심이다. 텍스트를 한 덩어리로 놓고 라벨 위치만 찾으면 **짧은 라벨이
본문 단어에 오탐한다** — 실제 관측된 본문에 `1년이상 경력직을 모집`이 있어서 `경력` 라벨이
거기 걸리고 본문이 잘린다. 줄로 끊으면 `경력직을 모집하고 있습니다`는 `경력`과 **같은 줄이
아니므로** 라벨로 오인되지 않는다. 마크업 종류는 여전히 몰라도 된다 — 표든 정의목록이든
라벨과 값은 다른 셀에 있고, 셀 경계는 태그이기 때문이다.

**확정 절차는 목록 파서와 동일하다** — 국내 IP에서:

    python -m src.pipeline --probe-detail K170082609300018

원본 HTML이 `probe/`에 덤프되고 추출 결과와 **비어 있는 필드 목록**이 출력된다.
비는 필드가 있으면 `LABELS`에 실제 라벨을 추가하면 된다(정규식 수정 불필요).
"""
from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass, field
from typing import Any

from config import settings
from src.sources.base import SourceError, clean, http_get
from src.sources.worknet import BASE, USER_AGENT, _throttle

NAME = "worknet_detail"

DETAIL_PATH = "/wk/a/b/1500/empDetailAuthView.do"
DETAIL_URL = BASE + DETAIL_PATH

# 목록 행 체크박스 value의 두 번째 칸(`정보구분`)이 들어가는 자리다. 관측값은 `VALIDATION`
# 하나뿐이지만, **목록이 준 href가 있으면 항상 그것을 쓴다** — 손으로 조립하는 것보다 안전하다.
DEFAULT_INFO_TYPE_CD = "VALIDATION"
DEFAULT_INFO_TYPE_GROUP = "tb_workinfoworknet"

SCRIPT_RE = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)
TAG_RE = re.compile(r"<[^>]+>")

# --- 라벨 정본 (2026-09-30 실제 공고 2건에서 관측) --------------------------
# key -> 화면 라벨. 라벨을 추가할 때 정규식은 건드릴 필요가 없다.
LABELS: dict[str, str] = {
    "duty": "직무내용",              # ★자유서술 본문. 요건 통계의 원재료
    "preferred": "우대사항",
    "worker_count": "근로자수",       # ★숫자. 소기업·1인 판정을 되살리는 값
    "license": "자격 면허",
    "major": "전공",
    "computer_skill": "컴퓨터 활용 능력",
    "employment_type": "근무 형태",
    "work_hours": "주 소정근로시간",
    "career": "경력",
    "education": "학력",
    "salary": "임금",
    "location": "근무지역",
}

# 값으로는 뽑지 않지만 **값 구간의 끝으로는 써야 하는** 라벨.
# 없으면 마지막 항목의 값이 페이지 하단(안내문·푸터)까지 삼킨다.
BOUNDARY_ONLY: tuple[str, ...] = (
    "모집요강", "지원자격", "근무조건", "전형방법", "접수기간", "접수방법", "제출서류",
    "기업정보", "기업형태", "산업", "업종", "홈페이지", "담당자", "채용담당", "복리후생",
    "사회보험", "퇴직급여", "우대조건", "기타사항", "지원방법", "고용형태", "모집인원",
    "목록", "이전글", "다음글", "인쇄", "스크랩", "신고하기",
)

# 값이 실질적으로 비어 있음을 나타내는 표기. 화면에서 `-`로 채워 오는 항목이 많다.
EMPTY_MARKS = frozenset({"-", "--", "없음", "해당없음", "미정"})

# 본문 상한. 판정 프롬프트용(`settings.RAW_TEXT_LIMIT`)이 아니라 **저장용**이라 넉넉하다.
# 요건 통계는 원문을 통째로 보관해야 스킬 사전을 고칠 때 소급 적용할 수 있다.
DUTY_CHAR_LIMIT = 4000
# 값 한 개가 가져갈 최대 줄 수.
#   본문(`직무내용`)은 여러 셀·여러 단락으로 나뉘어 오므로 넉넉하게 받는다.
#   나머지는 표의 한 칸이므로 **1줄**이다. 2줄 이상 받으면 마지막 항목의 값이 페이지
#   하단(푸터의 `개인정보처리방침 …`)을 삼킨다 — BOUNDARY_ONLY에 없는 문구는 경계가
#   되지 못하므로, 푸터를 전부 열거하는 대신 줄 수로 막는 게 확실하다.
#   실제로 어떤 항목이 2줄로 오는지는 `--probe-detail`의 추출표에서 드러난다.
DUTY_LINE_LIMIT = 60
VALUE_LINE_LIMIT = 1


def _norm_label(text: str) -> str:
    """라벨 비교용 정규화 — 공백·콜론·전각콜론 제거.

    같은 항목이 `자격 면허`와 `자격면허`로 모두 관측된다. 라벨 쪽과 줄 쪽을 같은 방식으로
    정규화해 비교하면 공백 변동을 규칙 없이 흡수할 수 있다.
    """
    return re.sub(r"[\s:：]+", "", text or "")


_LABEL_BY_NORM: dict[str, str] = {_norm_label(v): k for k, v in LABELS.items()}
_BOUNDARY_NORMS: frozenset[str] = frozenset(_norm_label(b) for b in BOUNDARY_ONLY)

# 라벨과 값이 한 줄에 붙어 오는 경우(`근로자수 : 5명`)를 잡는 패턴.
# **콜론을 필수로 요구한다.** 콜론 없이 접두사만 보면 본문 문장이 라벨로 오탐한다 —
# 관측된 본문의 `경력직을 모집하고 있습니다`가 `경력` 라벨로 걸려 본문이 통째로 잘린다.
# 콜론 요구는 그 오탐을 막으면서 실제 관측된 인라인 형태는 그대로 받는다.
_INLINE_RES: dict[str, re.Pattern[str]] = {
    key: re.compile(r"\s*".join(re.escape(c) for c in label.replace(" ", ""))
                    + r"\s*[:：]\s*(\S.*)$")
    for key, label in LABELS.items()
}


def to_lines(body: str) -> list[str]:
    """HTML → 표시 텍스트 줄 목록.

    script/style을 먼저 버린다 — 상세 페이지에는 D-day 계산·지도·통계 스크립트가 섞여
    있어서 태그만 지우면 JS 본문이 값 안으로 들어온다.
    그다음 **모든 태그를 줄바꿈으로** 바꾼다. 어떤 태그가 셀 경계인지 몰라도 되고,
    라벨과 값이 다른 태그 안에 있다는 사실만 이용하면 된다.
    """
    text = _html.unescape(TAG_RE.sub("\n", SCRIPT_RE.sub("\n", body or "")))
    lines = []
    for raw in text.split("\n"):
        line = clean(re.sub(r"[ \t ]+", " ", raw))
        if line:
            lines.append(line)
    return lines


@dataclass
class DetailInfo:
    """상세 페이지에서 뽑은 부가 정보. `JobPosting`을 대체하지 않고 **보강**한다.

    `JobPosting`에 필드를 더하지 않는 이유: 노션 스키마(15속성)와
    `test_notion.py::test_schema_has_every_property_publish_writes`가 그 모양에 묶여 있다.
    상세 정보는 `JobPosting.extra`로 실어 보내고, 노션에 새 열을 만들지는 사용자가 결정한다.
    """
    source_id: str
    url: str = ""
    duty: str = ""                       # ★자유서술 본문
    preferred: str = ""                  # 우대사항
    worker_count: int | None = None      # ★근로자수(명)
    license: str = ""
    major: str = ""
    computer_skill: str = ""
    employment_type: str = ""
    work_hours: str = ""
    career: str = ""
    education: str = ""
    salary: str = ""
    location: str = ""
    fields: dict[str, str] = field(default_factory=dict)   # 라벨별 원문(probe·디버깅용)

    # 통계용으로 원문 그대로 보관할 키들. 정규화하지 않는다.
    RAW_KEYS = ("duty", "preferred", "license", "computer_skill")

    def size_band(self) -> str | None:
        """근로자수 → 기업규모 밴드. **판정에 자동 반영하지 않는다.**

        `config/rules.py`의 `소기업·1인`은 사용자의 4대 판정 기준 중 하나이고, CLAUDE.md가
        "패턴의 등급을 바꾸는 것 = 사용자의 판정 기준을 바꾸는 것"이라고 못박았다. 그래서
        이 함수는 값을 **계산해 돌려주기만** 하고 어디에도 자동 연결하지 않는다.

        경계 근거: 사용자 기준의 문제는 '작은 회사'가 아니라 **선임 없이 혼자 전산을 떠맡는
        자리**다. 그래서 1명과 2~9명을 가른다.
        """
        n = self.worker_count
        if n is None:
            return None
        if n <= 1:
            return "소기업·1인"
        if n < 10:
            return "소기업"
        if n < 100:
            return "중소기업"
        if n < 1000:
            return "중견기업"
        return "대기업"

    def missing(self) -> list[str]:
        """비어 있는 필드 이름. probe 출력에서 라벨 보정이 필요한지 판별한다."""
        out = []
        for key in LABELS:
            if key == "worker_count":
                if self.worker_count is None:
                    out.append(key)
            elif not getattr(self, key, ""):
                out.append(key)
        return out

    def to_extra(self) -> dict[str, Any]:
        """`JobPosting.extra`에 병합할 형태. 원문 키는 가공하지 않고 그대로 넣는다."""
        extra: dict[str, Any] = {"detail_url": self.url}
        for key in self.RAW_KEYS:
            value = getattr(self, key, "")
            if value:
                extra[key] = value
        if self.worker_count is not None:
            extra["worker_count"] = self.worker_count
            extra["size_band"] = self.size_band()
        return extra


def _classify(line: str) -> tuple[str, str] | None:
    """줄이 라벨이면 (kind, key). kind는 'value' | 'stop'. 라벨이 아니면 None.

    판정 순서가 중요하다.
      1. 줄 전체가 라벨과 같다(표의 머리 칸) — 가장 흔한 형태.
      2. 경계 전용 라벨과 같다(섹션 제목).
      3. `라벨 : 값` 인라인 — **콜론이 있어야만** 라벨로 본다(`_INLINE_RES` 주석 참조).
    """
    norm = _norm_label(line)
    if norm in _LABEL_BY_NORM:
        return "value", _LABEL_BY_NORM[norm]
    if norm in _BOUNDARY_NORMS:
        return "stop", ""
    for key, pattern in _INLINE_RES.items():
        if pattern.match(line):
            return "value", key
    return None


def _inline_value(line: str, key: str) -> str:
    """`근로자수 : 5명` → `5명`. 라벨만 있는 줄이면 빈 문자열."""
    m = _INLINE_RES[key].match(line)
    return clean(m.group(1)) if m else ""


_NUM_RE = re.compile(r"(\d[\d,]*)")


def _to_count(value: str) -> int | None:
    """'5명' / '5 명' / '1,200명' → int. 숫자가 없으면 None(추측하지 않는다)."""
    m = _NUM_RE.search(value or "")
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return None


def extract(lines: list[str]) -> dict[str, str]:
    """줄 목록 → {key: 값}. 라벨 줄 이후부터 **다음 라벨 줄 직전까지**를 값으로 본다.

    같은 라벨이 여러 번 나오면 **처음 것만** 쓴다. 상세 페이지 하단의 유사 공고 목록이나
    안내 박스에 같은 라벨이 다시 나올 수 있는데, 위쪽이 본문이다.
    """
    out: dict[str, str] = {}
    i = 0
    while i < len(lines):
        tag = _classify(lines[i])
        if tag is None or tag[0] != "value":
            i += 1
            continue
        key = tag[1]
        if key in out:      # 처음 등장만 채택
            i += 1
            continue
        inline = _inline_value(lines[i], key)
        collected = [inline] if inline else []
        line_cap = DUTY_LINE_LIMIT if key == "duty" else VALUE_LINE_LIMIT
        j = i + 1
        while j < len(lines) and len(collected) < line_cap:
            if _classify(lines[j]) is not None:   # 다음 라벨/경계에서 멈춘다
                break
            collected.append(lines[j])
            j += 1
        value = clean(" ".join(collected))
        if key == "duty":
            value = value[:DUTY_CHAR_LIMIT]
        out[key] = "" if value in EMPTY_MARKS else value
        i = j if j > i else i + 1
    return out


def parse(body: str, source_id: str = "", url: str = "") -> DetailInfo:
    """상세 페이지 HTML → DetailInfo.

    목록 파서(`worknet.parse`)와 같은 원칙을 지킨다 — **조용히 빈 값을 돌려주지 않는다.**
    상세 페이지인지조차 판별되지 않으면 `SourceError`로 시끄럽게 실패해야, 매주 본문이
    비어도 이유를 알 수 있다.
    """
    lines = to_lines(body)
    if not lines:
        raise SourceError(
            "워크넷 상세 응답이 비어 있습니다 (`--probe-detail`로 덤프 확인 필요)")
    found = extract(lines)
    # 상세 페이지 판별: 값 라벨이 최소 2개는 잡혀야 한다. 로그인 유도·오류 페이지는
    # 라벨이 하나도 없고, 1개는 우연히 걸릴 수 있다.
    if len(found) < 2:
        raise SourceError(
            f"워크넷 상세 페이지의 라벨을 찾지 못했습니다(발견 {len(found)}개) — 상세 페이지가"
            " 아니거나 라벨이 바뀐 것으로 보입니다"
            " (`--probe-detail`로 덤프 확인 후 LABELS 보정)")

    info = DetailInfo(source_id=source_id, url=url)
    for key, value in found.items():
        info.fields[LABELS[key]] = value
        if key == "worker_count":
            info.worker_count = _to_count(value)
        else:
            setattr(info, key, value)
    return info


def split_query(url: str) -> tuple[str, dict[str, str]]:
    """`...do?a=1&b=2` → (`...do`, {"a": "1", "b": "2"}).

    ⚠️ **이 분해가 없으면 요청이 조용히 깨진다 (2026-10-01 실제 발생).**
    `http_get(url, {})`처럼 쿼리가 든 URL에 빈 params를 넘기면, httpx는 params를
    **merge가 아니라 replace**로 처리해서 **URL의 쿼리스트링을 전부 지운다.**

        httpx.Request("GET", ".../empDetailAuthView.do?wantedAuthNo=K…", params={})
        → 실제 요청 ".../empDetailAuthView.do"   (파라미터 없음)

    그러면 work24는 HTTP 200으로 883바이트짜리 스텁
    (`구인정보를 확인할 수 없습니다`)을 돌려주므로, 네트워크·차단·파서 어디를 봐도
    원인이 안 보인다. 요청 조건 7가지를 전부 실패로 오판하게 만든 버그였다.

    중복 키는 잃지만 상세 URL의 파라미터(`wantedAuthNo`·`infoTypeCd`·`infoTypeGroup`)에
    중복이 없어 무해하다.
    """
    from urllib.parse import parse_qsl, urlsplit
    parts = urlsplit(url)
    base = f"{parts.scheme}://{parts.netloc}{parts.path}" if parts.scheme else parts.path
    return base, dict(parse_qsl(parts.query))


def detail_url(source_id: str, info_type_cd: str = "", info_type_group: str = "") -> str:
    """공고번호로 상세 URL 조립. **목록이 준 href가 있으면 그것을 쓸 것.**"""
    from urllib.parse import urlencode
    return f"{DETAIL_URL}?" + urlencode({
        "wantedAuthNo": source_id,
        "infoTypeCd": info_type_cd or DEFAULT_INFO_TYPE_CD,
        "infoTypeGroup": info_type_group or DEFAULT_INFO_TYPE_GROUP,
    })


class WorknetDetailSource:
    """상세 페이지 수집기.

    `Source` 프로토콜(`fetch(keyword, role, limit)`)을 **구현하지 않는다** — 키워드로
    검색하는 소스가 아니라 목록이 준 공고번호를 받아 한 건씩 받는 보강 단계이므로,
    collector에 소스로 끼우면 계약이 맞지 않는다. 호출 지점은 목록 수집 **이후**다.
    """

    name = NAME

    def __init__(self, timeout: float | None = None):
        self.timeout = timeout if timeout is not None else settings.HTTP_TIMEOUT

    def _raw(self, url: str) -> str:
        # 목록과 **같은 전역 간격**을 쓴다(`worknet._throttle`). 상세는 공고 1건당 1요청이라
        # 목록보다 요청 수가 훨씬 많으므로, 간격을 건너뛰면 여기서 버스트가 난다.
        _throttle()
        base, params = split_query(url)
        _ctype, body = http_get(
            base, params, timeout=self.timeout,
            headers={"User-Agent": USER_AGENT,
                     "Accept": "text/html,application/xhtml+xml"},
        )
        return body

    def probe(self, source_id: str) -> tuple[str, str, str]:
        """(확장자, 원본 HTML, 요청 URL) — `--probe-detail`이 덤프하는 자리."""
        url = source_id if source_id.startswith("http") else detail_url(source_id)
        return "html", self._raw(url), url

    def fetch_one(self, source_id: str, url: str = "") -> DetailInfo:
        """공고 1건의 상세 정보. 실패는 `SourceError`/`SourceUnreachable`로 올린다."""
        target = url or detail_url(source_id)
        return parse(self._raw(target), source_id=source_id, url=target)

    def enrich(self, postings: list[Any], *, limit: int | None = None, log=print) -> int:
        """`JobPosting` 리스트의 `extra`에 상세 정보를 병합. 성공 건수를 반환.

        **개별 실패가 전체를 막지 않는다** — `collector.collect`와 같은 원칙이다. 상세는
        보강이므로 실패해도 목록 기준 판정은 그대로 나가야 한다.
        """
        done = 0
        for posting in (postings[:limit] if limit else postings):
            try:
                info = self.fetch_one(posting.source_id, getattr(posting, "url", "") or "")
            except SourceError as e:
                log(f"  [경고] 상세 {posting.source_id} 실패: {e}")
                continue
            posting.extra.update(info.to_extra())
            done += 1
        return done
