"""제목 프리스크린 — 본판정 **전에** 목록을 줄인다.

사용자 요구(2026-08-10): "모든 내용을 다 점검하면 제미나이에 무리가 가니 제목만 보고
추리하자. 맞지 않는 공고는 그냥 버리고 관심 갈 만한 기업만 추천해라. 그러면 DB에 데이터가
많이 쌓이지 않는다."

그래서 파이프라인이 이렇게 바뀐다:

    수집 → 신호(정규식) → **프리스크린(제목만)** → 본판정(전체 필드) → 노션 발행

프리스크린을 통과하지 못한 공고는 **노션에 아예 만들어지지 않는다.** 본판정도 안 받는다.
줄어드는 건 DB 행 수만이 아니라 LLM에 들어가는 토큰이기도 하다 — 공고 1건이 프리스크린에서는
한 줄(약 30자)이지만 본판정에서는 `RAW_TEXT_LIMIT`(1200자)까지 간다.

**설계상 지켜야 할 두 가지**

1. **규칙이 먼저다.** `sig.forced`(3교대·24/365 등 정규식이 확정한 위험)는 LLM에 묻지도 않고
   여기서 버린다. 코드가 확신하는 건을 모델에게 되물을 이유가 없고, 그게 이 프로젝트의
   "규칙이 이긴다" 원칙과 같은 방향이다.
2. **실패는 통과 쪽으로 연다(fail-open).** 프리스크린 호출이 깨졌을 때 전부 버리면 그 주의
   구직 기회가 조용히 사라진다. 배치 하나가 실패하면 **그 배치는 전부 살려서** 본판정으로
   넘긴다. 비용이 조금 더 드는 쪽이 기회를 잃는 쪽보다 낫다.

애매한 건도 살리는 쪽으로 프롬프트를 잡았다. 제목만으로는 근무형태·회사 규모를 알 수 없어서
여기서 정밀 판정을 시도하면 놓치는 게 생긴다 — 정밀 판정은 다음 단계의 일이다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from config import roles, settings
from src.evaluator import CallFn, EvaluationError, _model_chain, chunks, make_gemini_call
from src.signals import SignalResult
from src.sources.base import JobPosting

SYSTEM_PROMPT = (
    "당신은 한국 IT 인프라/운영 트랙 구직자의 **1차 선별** 보조자다. "
    "채용공고의 회사명과 제목만 보고, 본심사로 넘길 가치가 있는 것만 남긴다.\n"
    "구직자가 찾는 일: 클라우드/시스템/서버/네트워크/전산 인프라의 운영·구축·기술지원.\n"
    "남긴다:\n"
    "- 위 분야의 엔지니어·운영·구축·기술지원·전산 담당 공고\n"
    "- 제목만으로는 무슨 일인지 애매한 IT 공고 (놓치는 것보다 한 번 더 보는 게 낫다)\n"
    "버린다:\n"
    "- 직무가 아예 다른 공고: 영업, 생산·제조, 배송·운전, 요양·간병, 미화, 조리, 강사,\n"
    "  단순사무, 상담, 판매 등 — 검색 키워드만 우연히 걸린 것들\n"
    "- 개발 전용 공고(웹/앱/프론트엔드/백엔드 개발자 모집) — 이 구직자의 트랙이 아니다\n"
    "- 제목에 24시간 관제·모니터링 상주·교대근무가 드러난 공고\n"
    "- 파견·도급 인력 모집, 아르바이트·일용직\n"
    "판단 규칙:\n"
    "- 제목만으로는 회사 규모나 근무형태를 알 수 없다. **추측해서 버리지 마라.**\n"
    "- 애매하면 남긴다. 확실히 아닌 것만 버린다.\n"
    "- 남길 항목의 번호만 JSON으로 반환한다. 예: {\"keep\": [1, 4, 7]}"
)

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"keep": {"type": "array", "items": {"type": "integer"}}},
    "required": ["keep"],
}


@dataclass
class Dropped:
    """프리스크린에서 버려진 공고 1건 (로그·요약용. 노션에는 만들지 않는다)."""
    source_key: str
    company: str
    title: str
    reason: str


def _line(no: int, job: JobPosting) -> str:
    """프리스크린 입력 한 줄. 여기에 필드를 더 붙이지 말 것 — '제목만'이 이 단계의 요점이다."""
    return f"{no}. [{roles.display_name(job.role)}] {job.company} | {job.title}"


def build_prompt(batch: list[JobPosting]) -> str:
    body = "\n".join(_line(i, job) for i, job in enumerate(batch, start=1))
    return (f"다음 채용공고 {len(batch)}건 중 본심사로 넘길 것의 번호만 고르라.\n"
            f"번호는 1부터 {len(batch)}까지다.\n\n{body}")


def _parse_keep(text: str, size: int) -> set[int]:
    """응답 → 남길 번호 집합(1-based). 범위 밖 번호는 버린다."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise EvaluationError(f"프리스크린 JSON 파싱 실패: {e} / 앞부분={text[:200]!r}") from e
    if isinstance(data, list):          # 스키마를 무시하고 배열만 준 경우도 받아준다
        raw = data
    elif isinstance(data, dict):
        raw = data.get("keep", [])
    else:
        raise EvaluationError(f"프리스크린 응답 형식 이상: {type(data).__name__}")
    if not isinstance(raw, list):
        raise EvaluationError(f"keep이 배열이 아님: {type(raw).__name__}")
    return {n for n in (_as_int(v) for v in raw) if n is not None and 1 <= n <= size}


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _keep_numbers(batch: list[JobPosting], call: CallFn, log) -> set[int] | None:
    """배치 1개 선별. 모델 체인을 순서대로 시도하고, 전부 실패하면 None(=통과).

    폴백이 특히 중요한 이유: 무료 티어는 **모델별로** 일일 요청 수를 따로 센다.
    기본 모델이 하루치를 다 쓰면 lite 모델에는 아직 남아 있는 경우가 많다.
    """
    prompt = build_prompt(batch)
    for model in _model_chain():
        try:
            return _parse_keep(call(model, prompt), len(batch))
        except Exception as e:  # noqa: BLE001 — 다음 모델, 그다음엔 통과
            log(f"  [경고] 프리스크린 실패({model}, {len(batch)}건): {type(e).__name__}: {e}")
    log(f"  [폴백] {len(batch)}건은 선별 없이 본판정으로 넘김")
    return None


def prescreen(jobs: list[JobPosting], sigs: dict[str, SignalResult], *,
              use_llm: bool = True, call: CallFn | None = None,
              batch_size: int | None = None,
              log=print) -> tuple[list[JobPosting], list[Dropped]]:
    """(남길 공고, 버린 공고) 반환. 원래 순서를 보존한다."""
    if not jobs:
        return [], []

    kept: list[JobPosting] = []
    dropped: list[Dropped] = []

    # 1) 규칙이 확정한 위험은 LLM에 묻지 않는다 — 코드가 이미 확신하는 건이다.
    candidates: list[JobPosting] = []
    for job in jobs:
        sig = sigs.get(job.source_key, SignalResult())
        if sig.forced:
            dropped.append(Dropped(job.source_key, job.company, job.title,
                                   f"규칙 확정 위험: {'; '.join(sig.forced_by)}"))
        else:
            candidates.append(job)

    if not use_llm:
        # 규칙만으로 돌릴 땐 강제 위험만 걸러내고 나머지는 그대로 통과시킨다.
        return candidates, dropped

    call = call or make_gemini_call(SYSTEM_PROMPT, RESPONSE_SCHEMA,
                                    settings.PRESCREEN_MAX_TOKENS)
    size = batch_size or settings.PRESCREEN_BATCH_SIZE

    for batch in chunks(candidates, size):
        keep_nos = _keep_numbers(batch, call, log)
        if keep_nos is None:                        # 전 모델 실패 → 통과 쪽으로 연다
            kept.extend(batch)
            continue
        for i, job in enumerate(batch, start=1):
            if i in keep_nos:
                kept.append(job)
            else:
                dropped.append(Dropped(job.source_key, job.company, job.title,
                                       "제목 선별에서 제외(직무 불일치)"))

    return kept, dropped


def summary(kept: list[JobPosting], dropped: list[Dropped]) -> str:
    total = len(kept) + len(dropped)
    by_rule = sum(1 for d in dropped if d.reason.startswith("규칙 확정"))
    return (f"프리스크린 {total}건 → 통과 {len(kept)}건 / 제외 {len(dropped)}건"
            f" (규칙 확정 위험 {by_rule}건, 제목 선별 {len(dropped) - by_rule}건)")
