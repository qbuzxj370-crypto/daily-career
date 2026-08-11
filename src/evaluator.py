"""Gemini 배치 판정. 공고 N건 -> Verdict N건 (JSON 모드로 구조 보장).

설계 원칙(docs/career-plan.md §아키텍처 결정 4·5):
  - **규칙이 먼저, LLM이 나중, 그리고 규칙이 이긴다.** signals가 forced=True로 확정한 건은
    LLM이 무엇을 반환하든 코드가 verdict를 `위험`으로 되돌린다. 모델은 여기서 요약기이지
    판정 권한자가 아니다.
  - **배치 호출.** 40건 개별 호출은 무료 티어 RPM에 걸린다. EVAL_BATCH_SIZE씩 배열로 받고,
    배치가 실패하면 그 배치만 개별 호출 → 그래도 실패하면 폴백 모델 → 최후엔 규칙 판정.
  - **사원수는 API가 주지 않는다.** 그래서 company_size에 `판단 불가`를 허용하고, 그 값이
    오면 본문에 그대로 노출해 사람이 노션에서 확인하게 한다. 추측으로 메우지 않는다.
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from config import roles, settings
from src.signals import SignalResult
from src.sources.base import JobPosting

VERDICTS = ["적합", "보통", "위험"]
COMPANY_SIZES = ["대기업", "중견기업", "중소기업", "소기업·1인", "판단 불가"]

# LLM 호출 함수 계약: (model, prompt) -> 응답 텍스트(JSON)
CallFn = Callable[[str, str], str]


class EvaluationError(RuntimeError):
    pass


@dataclass
class Verdict:
    source_key: str
    verdict: str = "보통"
    score: int = 3
    summary: str = ""
    positive_signals: list[str] = field(default_factory=list)
    risk_signals: list[str] = field(default_factory=list)
    reason: str = ""
    company_size: str = "판단 불가"
    by_rule: bool = False          # 규칙이 verdict를 확정/강제했는가
    llm_failed: bool = False       # LLM 판정 실패로 규칙 판정으로 대체됐는가

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_key": self.source_key, "verdict": self.verdict, "score": self.score,
            "summary": self.summary, "positive_signals": self.positive_signals,
            "risk_signals": self.risk_signals, "reason": self.reason,
            "company_size": self.company_size,
        }


# Gemini JSON 모드 응답 스키마(OpenAPI 서브셋: minLength/minItems 미지원)
RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "source_key": {"type": "string"},
            "verdict": {"type": "string", "enum": VERDICTS},
            "score": {"type": "integer"},
            "summary": {"type": "string"},
            "positive_signals": {"type": "array", "items": {"type": "string"}},
            "risk_signals": {"type": "array", "items": {"type": "string"}},
            "reason": {"type": "string"},
            "company_size": {"type": "string", "enum": COMPANY_SIZES},
        },
        "required": ["source_key", "verdict", "score", "summary", "reason"],
    },
}

SYSTEM_PROMPT = (
    "당신은 한국 IT 인프라/운영 트랙 구직자의 채용공고 심사 보조자다. "
    "공고 목록을 받아 각 건이 이 구직자에게 맞는지 판정한다.\n"
    "판정 기준(이 4개가 전부다. 임의로 다른 기준을 만들지 마라):\n"
    "- 🔴 위험: 24/365 관제 전담, 교대근무, 모니터링 상주 — 커리어가 정체된다.\n"
    "- 🔴 위험: 소기업·오너 1인 체제(전산 담당 혼자, 사수 없음, 대표 직속).\n"
    "- 🟢 가점: 구축 조직(구축팀·SI)과 같은 공간에서 일해 구축 경험을 얻을 수 있다.\n"
    "- 🟢 가점: 상시 주간 근무, 교대 없음.\n"
    "출력 규칙:\n"
    "- verdict는 정확히 '적합' / '보통' / '위험' 중 하나. score는 1~5 정수(5가 가장 적합).\n"
    "- summary는 공고가 실제로 무슨 일을 시키는지 1문장(40자 내외). 홍보문구를 베끼지 마라.\n"
    "- reason은 그 판정을 내린 근거 1~2문장.\n"
    "- positive_signals / risk_signals에는 위 4개 기준의 이름만 넣는다: "
    "'구축 조직 동거', '주간 중심', '관제 전담', '소기업·1인'.\n"
    "- 근거가 공고 텍스트에 없으면 지어내지 마라. 특히 **회사 규모는 대부분 정보가 없다** — "
    "그럴 때 company_size는 반드시 '판단 불가'로 둔다(추측 금지).\n"
    "- 입력의 [규칙 확정] 표시는 코드가 정규식으로 확정한 사실이다. 뒤집지 말고 근거로 삼아라.\n"
    "- 마크다운 표(`| ... |`)와 코드펜스를 쓰지 마라. 평문 문장만 쓴다.\n"
    "- 입력에 준 source_key를 **그대로** 돌려주고, 입력 건수만큼의 JSON 배열 하나만 반환한다."
)


def chunks(items: list, size: int) -> Iterable[list]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _job_block(job: JobPosting, sig: SignalResult) -> str:
    lines = [
        f"- source_key: {job.source_key}",
        f"  직무(검색 slug): {job.role} ({roles.display_name(job.role)})",
        f"  회사: {job.company}",
        f"  제목: {job.title}",
    ]
    if job.location:
        lines.append(f"  지역: {job.location}")
    if job.experience:
        lines.append(f"  경력: {job.experience}")
    if job.employment_type:
        lines.append(f"  고용형태: {job.employment_type}")
    text = job.raw_text[:settings.RAW_TEXT_LIMIT]
    lines.append(f"  공고텍스트: {text}")
    if sig.forced_by:
        lines.append(f"  [규칙 확정 — 위험] {'; '.join(sig.forced_by)}")
    elif sig.risk:
        lines.append(f"  [규칙 관측 — 위험 후보(완화됨)] {', '.join(sig.risk)}")
    if sig.positive:
        lines.append(f"  [규칙 관측 — 가점] {', '.join(sig.positive)}")
    return "\n".join(lines)


def build_prompt(jobs: list[JobPosting], sigs: dict[str, SignalResult]) -> str:
    body = "\n".join(_job_block(j, sigs.get(j.source_key, SignalResult())) for j in jobs)
    return (f"다음 채용공고 {len(jobs)}건을 각각 판정하라. "
            f"JSON 배열의 길이는 정확히 {len(jobs)}이어야 한다.\n\n{body}")


# ---------------------------------------------------------------- LLM 호출

def make_gemini_call(system_prompt: str, schema: dict[str, Any],
                     max_tokens: int) -> CallFn:
    """(system, schema)를 고정한 CallFn 생성. 프리스크린이 같은 호출 경로를 재사용한다."""
    def _call(model: str, prompt: str) -> str:
        from google import genai  # 지연 import: --mock/--no-llm 경로는 미설치여도 동작
        from google.genai import types

        client = genai.Client(api_key=settings.GEMINI_API_KEY)
        resp = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                max_output_tokens=max_tokens,
                response_mime_type="application/json",
                response_schema=schema,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )
        if not resp.text:
            raise EvaluationError("Gemini 응답이 비어 있음(차단/토큰초과 가능)")
        return resp.text
    return _call


_call_gemini = make_gemini_call(SYSTEM_PROMPT, RESPONSE_SCHEMA, settings.MAX_TOKENS)


def _model_chain() -> list[str]:
    chain = [settings.MODEL]
    if settings.MODEL_FALLBACK and settings.MODEL_FALLBACK != settings.MODEL:
        chain.append(settings.MODEL_FALLBACK)
    return chain


def _parse(text: str, expected: list[JobPosting]) -> dict[str, Verdict]:
    """응답 JSON 배열 -> {source_key: Verdict}. 입력에 없는 key는 버린다."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise EvaluationError(f"JSON 파싱 실패: {e} / 앞부분={text[:200]!r}") from e
    if isinstance(data, dict):
        data = data.get("results") or data.get("items") or [data]
    if not isinstance(data, list):
        raise EvaluationError(f"JSON 최상위가 배열이 아님: {type(data).__name__}")

    wanted = {j.source_key for j in expected}
    out: dict[str, Verdict] = {}
    for raw in data:
        if not isinstance(raw, dict):
            continue
        key = str(raw.get("source_key", "")).strip()
        if key not in wanted:
            continue
        out[key] = Verdict(
            source_key=key,
            verdict=raw["verdict"] if raw.get("verdict") in VERDICTS else "보통",
            score=_clamp_score(raw.get("score")),
            summary=str(raw.get("summary", "")).strip(),
            positive_signals=[s for s in raw.get("positive_signals", []) if isinstance(s, str)],
            risk_signals=[s for s in raw.get("risk_signals", []) if isinstance(s, str)],
            reason=str(raw.get("reason", "")).strip(),
            company_size=(raw.get("company_size") if raw.get("company_size") in COMPANY_SIZES
                          else "판단 불가"),
        )
    if not out:
        raise EvaluationError(f"응답에서 유효한 판정 0건(요청 {len(expected)}건)")
    return out


def _clamp_score(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 3
    return max(1, min(5, n))


# ---------------------------------------------------------------- 규칙 판정

def rule_verdict(job: JobPosting, sig: SignalResult, *, note: str = "") -> Verdict:
    """LLM 없이 규칙만으로 판정(--no-llm, LLM 실패 시 대체).

    가점 2개 + 위험 없음이면 적합, 강제 위험이면 위험, 나머지는 보통. 요약은 만들지 않는다
    (규칙은 요약을 못 한다 — 없는 것을 있는 척하지 않는다).
    """
    if sig.forced:
        v, score = "위험", 1
        reason = f"규칙 확정: {'; '.join(sig.forced_by)}"
    elif len(sig.positive) >= 2 and not sig.risk:
        v, score = "적합", 4
        reason = f"규칙 관측 가점: {', '.join(sig.positive)}"
    elif sig.risk:
        v, score = "보통", 2
        reason = f"위험 후보 관측(완화됨): {', '.join(sig.risk)}"
    elif sig.positive:
        v, score = "보통", 3
        reason = f"규칙 관측 가점: {', '.join(sig.positive)} (가점 1개 — 적합 판정에는 부족)"
    else:
        v, score = "보통", 3
        reason = "규칙에 걸린 신호 없음"
    if note:
        reason = f"{note} — {reason}"
    return Verdict(
        source_key=job.source_key, verdict=v, score=score,
        summary="(규칙 판정 — 요약 없음)",
        positive_signals=list(sig.positive), risk_signals=list(sig.risk),
        reason=reason, company_size="판단 불가",
        by_rule=True, llm_failed=bool(note),
    )


def apply_rules(v: Verdict, sig: SignalResult) -> Verdict:
    """규칙이 확정한 신호로 LLM 출력을 덮어쓴다. **규칙이 이긴다.**"""
    # 신호 목록은 코드가 정본(노션 Signals 속성의 원천) — LLM이 만든 이름은 규칙 이름과 합집합.
    v.positive_signals = _merge(sig.positive, v.positive_signals)
    v.risk_signals = _merge(sig.risk, v.risk_signals)
    if sig.forced and v.verdict != "위험":
        v.reason = f"[규칙 강제 위험] {'; '.join(sig.forced_by)} / LLM 판정: {v.verdict} — {v.reason}"
        v.verdict = "위험"
        v.by_rule = True
    elif sig.forced:
        v.by_rule = True
    if v.verdict == "위험":
        v.score = min(v.score, 2)  # verdict와 점수가 어긋나 정렬이 뒤집히는 것 방지
    return v


def _merge(primary: list[str], extra: list[str]) -> list[str]:
    out = list(primary)
    known = set(primary)
    for s in extra:
        if s not in known:
            out.append(s)
            known.add(s)
    return out


# ---------------------------------------------------------------- 엔트리

def evaluate(jobs: list[JobPosting], sigs: dict[str, SignalResult], *,
             use_llm: bool = True, call: CallFn | None = None,
             batch_size: int | None = None, log=print) -> dict[str, Verdict]:
    """공고 리스트 -> {source_key: Verdict}. 규칙 강제 적용까지 끝난 상태로 반환."""
    if not jobs:
        return {}
    if not use_llm:
        return {j.source_key: apply_rules(rule_verdict(j, _sig(sigs, j)), _sig(sigs, j))
                for j in jobs}

    call = call or _call_gemini
    size = batch_size or settings.EVAL_BATCH_SIZE
    out: dict[str, Verdict] = {}
    for batch in chunks(jobs, size):
        out.update(_judge_group(batch, sigs, call, log))

    return {j.source_key: apply_rules(out[j.source_key], _sig(sigs, j)) for j in jobs}


def _sig(sigs: dict[str, SignalResult], job: JobPosting) -> SignalResult:
    return sigs.get(job.source_key, SignalResult())


def _is_quota_exhausted(e: Exception) -> bool:
    """일일/분당 할당량 소진 오류인가.

    무료 티어는 **모델당 하루 20요청**이다(2026-08-10 실측: 429 RESOURCE_EXHAUSTED,
    quotaId=GenerateRequestsPerDayPerProjectPerModel-FreeTier, limit 20 — flash와
    flash-lite가 각각 20). 이 상태에서 개별 재시도를 돌리면 확실히 실패할 호출을 10번
    더 쏘면서 시간만 쓴다. 파싱 실패 같은 다른 오류와 달리 개별 호출로 나눈다고 풀리는
    문제가 아니므로, 이 모델은 접고 바로 다음 모델로 넘어간다.
    """
    text = str(e)
    return "RESOURCE_EXHAUSTED" in text or "429" in text


def _judge_group(batch: list[JobPosting], sigs: dict[str, SignalResult],
                 call: CallFn, log) -> dict[str, Verdict]:
    """배치 1개 판정. 배치 실패 → 개별 재시도 → 폴백 모델 → 규칙 판정."""
    got: dict[str, Verdict] = {}
    for model in _model_chain():
        pending = [j for j in batch if j.source_key not in got]
        if not pending:
            break
        try:
            got.update(_parse(call(model, build_prompt(pending, sigs)), pending))
            continue
        except Exception as e:  # noqa: BLE001 — 배치 실패는 개별 호출로 격하
            if _is_quota_exhausted(e):
                log(f"  [경고] {model} 할당량 소진({len(pending)}건) — 개별 재시도를 건너뛰고"
                    f" 다음 모델로: {e}")
                continue
            log(f"  [경고] 배치 판정 실패({model}, {len(pending)}건): {type(e).__name__}: {e}"
                f" → 개별 호출로 재시도")
        for job in [j for j in batch if j.source_key not in got]:
            try:
                got.update(_parse(call(model, build_prompt([job], sigs)), [job]))
            except Exception as e:  # noqa: BLE001 — 다음 모델 또는 규칙 판정으로
                log(f"    [경고] 개별 판정 실패({model}) {job.source_key}: {type(e).__name__}: {e}")

    for job in batch:
        if job.source_key not in got:
            log(f"    [폴백] 규칙 판정으로 대체: {job.source_key}")
            got[job.source_key] = rule_verdict(job, _sig(sigs, job), note="LLM 판정 실패")
    return got
