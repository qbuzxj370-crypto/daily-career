"""워크넷/고용24 채용정보 API 클라이언트 (공식 API. 스크래핑 없음).

  GET https://openapi.work.go.kr/opi/opi/opia/wantedApi.do
      ?authKey=...&callTp=L&returnType=XML&startPage=1&display=N&keyword=...

⚠️ **P1 미검증.** 이 소스는 사람인보다 불확실성이 크다 — 엔드포인트가 work.go.kr 직접이냐
data.go.kr 경유냐에 따라 **인증 파라미터명(authKey vs serviceKey)과 응답 포맷(XML/JSON)이
달라진다.** 그래서 이 클라이언트는:
  - URL(`WORKNET_API_URL`)·인증 파라미터명(`WORKNET_AUTH_PARAM`)·추가 파라미터
    (`WORKNET_EXTRA_PARAMS`, "k=v&k2=v2")를 env로 바꿀 수 있고,
  - XML/JSON 응답을 **둘 다** 파싱한다.
`--probe worknet --role public_it`로 실제 응답을 덤프해 `FIELDS`를 확정하라.
"""
from __future__ import annotations
import json
import os
from typing import Any
from xml.etree import ElementTree as ET

from config import settings
from src.sources.base import (
    JobPosting, SourceError, build_raw_text, clean, first_str,
    http_get, norm_date,
)

NAME = "worknet"

AUTH_PARAM = os.environ.get("WORKNET_AUTH_PARAM", "authKey")

# 평탄화된 항목(dict) 기준 후보 키. 앞쪽 우선.
FIELDS: dict[str, tuple[str, ...]] = {
    "id": ("wantedAuthNo", "empSeqno", "wantedMngNo", "jobId"),
    "title": ("title", "wantedTitle", "empWantedTitle"),
    "company": ("company", "coNm", "empBusiNm"),
    "url": ("wantedInfoUrl", "wantedMobileInfoUrl", "empWantedHomepgDetail"),
    "location": ("region", "basicAddr", "workRegionNm"),
    "experience": ("career", "careerNm", "empWantedCareerNm"),
    "employment_type": ("empTpNm", "empWantedTypeNm", "empTpCdNm"),
    "deadline": ("closeDt", "empWantedEndDt", "toDate"),
    "job_category": ("jobsNm", "jobsCd", "empWantedJobNm"),
    "education": ("minEdubg", "minEdubgNm"),
    "salary": ("sal", "salTpNm", "salNm"),
    "holiday": ("holidayTpNm", "workDay"),
    "detail": ("jobsDetail", "detailContents", "empWantedContents"),
}


def _flatten(elem: ET.Element) -> dict[str, str]:
    """<wanted> 하위 태그를 {태그명: 텍스트}로 평탄화(1단계 하위까지)."""
    out: dict[str, str] = {}
    for child in elem:
        text = (child.text or "").strip()
        if len(child):  # 손자가 있으면 태그명.손자명으로 한 단계 더
            for gc in child:
                out[f"{child.tag}.{gc.tag}"] = (gc.text or "").strip()
                out.setdefault(gc.tag, (gc.text or "").strip())
        if text:
            out[child.tag] = text
    return out


class WorknetSource:
    name = NAME

    def __init__(self, auth_key: str | None = None, url: str | None = None,
                 timeout: float | None = None, return_type: str = "XML"):
        self.auth_key = auth_key if auth_key is not None else settings.DATA_GO_KR_KEY
        self.url = url or settings.WORKNET_API_URL
        self.timeout = timeout if timeout is not None else settings.HTTP_TIMEOUT
        self.return_type = return_type

    # ---------------------------------------------------------------- HTTP
    def _params(self, keyword: str, display: int, start_page: int = 1) -> dict[str, Any]:
        params: dict[str, Any] = {
            AUTH_PARAM: self.auth_key,
            "callTp": "L",            # L = 목록
            "returnType": self.return_type,
            "startPage": start_page,
            "display": display,
            "keyword": keyword,
        }
        for pair in os.environ.get("WORKNET_EXTRA_PARAMS", "").split("&"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                params[k.strip()] = v.strip()
        return params

    def _raw(self, keyword: str, display: int) -> str:
        if not self.auth_key:
            raise SourceError("DATA_GO_KR_KEY 미설정 (공공데이터포털 고용24 채용정보 활용신청 후 .env에 등록)")
        _ctype, body = http_get(self.url, self._params(keyword, display), timeout=self.timeout,
                                headers={"Accept": "application/xml, application/json"})
        return body

    def probe(self, keyword: str) -> tuple[str, str]:
        body = self._raw(keyword, display=3)
        return ("json" if body.lstrip().startswith(("{", "[")) else "xml"), body

    # ---------------------------------------------------------------- 파싱
    def fetch(self, keyword: str, role: str, limit: int) -> list[JobPosting]:
        return self.parse(self._raw(keyword, display=limit), role)[:limit]

    @staticmethod
    def items(body: str) -> list[dict[str, str]]:
        """XML/JSON 어느 쪽이 와도 평탄화된 dict 리스트로."""
        text = body.strip()
        if text.startswith(("{", "[")):
            payload = json.loads(text)
            node: Any = payload
            for key in ("wantedRoot", "wanted", "items", "item", "body", "response"):
                if isinstance(node, dict) and key in node:
                    node = node[key]
            if isinstance(node, dict):
                node = [node]
            if not isinstance(node, list):
                return []
            return [{k: ("" if v is None else str(v)) for k, v in it.items()}
                    for it in node if isinstance(it, dict)]
        try:
            root = ET.fromstring(text)
        except ET.ParseError as e:
            raise SourceError(f"워크넷 응답 파싱 실패(XML/JSON 모두 아님): {e} / "
                              f"앞부분={text[:200]!r}") from e
        # 공공데이터포털 공통 오류 봉투(<cmmMsgHeader><returnAuthMsg>...)를 승격
        err = root.find(".//returnAuthMsg")
        if err is not None and (err.text or "").strip():
            raise SourceError(f"워크넷 API 오류: {err.text.strip()}")
        return [_flatten(w) for w in root.iter("wanted")] or \
               [_flatten(w) for w in root.iter("item")]

    @classmethod
    def parse(cls, body: str, role: str) -> list[JobPosting]:
        return [p for p in (cls._to_posting(it, role) for it in cls.items(body)) if p]

    @classmethod
    def _to_posting(cls, it: dict[str, str], role: str) -> JobPosting | None:
        f = FIELDS
        source_id = first_str(it, *f["id"])
        title = clean(first_str(it, *f["title"]))
        if not source_id or not title:
            print(f"  [경고] worknet: id/title 추출 실패, 1건 건너뜀 (키: {sorted(it)[:8]})")
            return None
        location = clean(first_str(it, *f["location"])) or None
        experience = clean(first_str(it, *f["experience"])) or None
        emp_type = clean(first_str(it, *f["employment_type"])) or None
        company = clean(first_str(it, *f["company"])) or "(회사명 미상)"
        return JobPosting(
            source=NAME,
            source_id=source_id,
            title=title,
            company=company,
            url=first_str(it, *f["url"]),
            role=role,
            location=location,
            experience=experience,
            employment_type=emp_type,
            deadline=norm_date(first_str(it, *f["deadline"]) or None),
            raw_text=build_raw_text(
                title, company, first_str(it, *f["job_category"]), emp_type,
                location, experience, first_str(it, *f["education"]),
                first_str(it, *f["salary"]), first_str(it, *f["holiday"]),
                first_str(it, *f["detail"])[:settings.RAW_TEXT_LIMIT],
            ),
            extra={"holiday": first_str(it, *f["holiday"])},
        )
