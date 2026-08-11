"""제목 프리스크린 검증 — 무엇을 버리고, 실패했을 때 어느 쪽으로 여는가.

프리스크린은 공고를 **영구히 사라지게 하는** 유일한 단계다(버려지면 노션에 안 남는다).
그래서 두 가지가 테스트로 고정돼야 한다: 규칙 확정 위험은 LLM 없이 버린다는 것,
그리고 호출이 깨졌을 때 조용히 다 버리는 게 아니라 전부 통과시킨다는 것.
"""
from __future__ import annotations
import json

from src import prescreen, signals
from src.pipeline import _mock_jobs, _publish_flow
from src.sources.base import JobPosting
from tests.test_pipeline import FakeState


def _jobs():
    return _mock_jobs()


def _count_items(prompt: str) -> int:
    return sum(1 for line in prompt.splitlines() if line[:1].isdigit() and ". " in line)


def test_forced_risk_is_dropped_without_asking_the_llm():
    """3교대·1인 전산처럼 규칙이 확정한 건은 모델에 묻지 않는다."""
    calls = []

    def call(model, prompt):
        calls.append(prompt)
        return json.dumps({"keep": list(range(1, _count_items(prompt) + 1))})

    jobs = _jobs()
    sigs = signals.analyze_all(jobs)
    kept, dropped = prescreen.prescreen(jobs, sigs, call=call, log=lambda *a: None)

    forced = {k for k, s in sigs.items() if s.forced}
    assert forced, "픽스처에 규칙 강제 위험이 하나도 없으면 이 테스트는 무의미하다"
    assert forced.isdisjoint({j.source_key for j in kept})
    assert forced <= {d.source_key for d in dropped}
    # 강제 위험 건은 프롬프트에 실리지도 않아야 한다(토큰을 쓰지 않는다).
    assert all("3교대" not in p for p in calls)


def test_llm_failure_keeps_the_whole_batch():
    """실패는 통과 쪽으로 연다 — 다 버리면 그 주의 구직 기회가 조용히 사라진다."""
    def boom(model, prompt):
        raise RuntimeError("429 rate limit")

    jobs = _jobs()
    sigs = signals.analyze_all(jobs)
    kept, dropped = prescreen.prescreen(jobs, sigs, call=boom, log=lambda *a: None)

    not_forced = [j for j in jobs if not sigs[j.source_key].forced]
    assert [j.source_key for j in kept] == [j.source_key for j in not_forced]
    assert all(d.reason.startswith("규칙 확정") for d in dropped)


def test_falls_back_to_the_second_model_before_giving_up():
    """무료 티어 일일 한도는 모델별로 따로 센다 — 기본 모델이 막혀도 lite에는 남아 있다."""
    from config import settings
    seen = []

    def call(model, prompt):
        seen.append(model)
        if model == settings.MODEL:
            raise RuntimeError("429 RESOURCE_EXHAUSTED")
        return json.dumps({"keep": [1]})

    jobs = _jobs()
    sigs = signals.analyze_all(jobs)
    kept, _ = prescreen.prescreen(jobs, sigs, call=call, log=lambda *a: None)
    assert seen == [settings.MODEL, settings.MODEL_FALLBACK]
    assert len(kept) == 1                       # 폴백 모델의 선별 결과가 실제로 적용됐다


def test_only_selected_numbers_survive_and_order_is_preserved():
    jobs = _jobs()
    sigs = signals.analyze_all(jobs)
    survivors = [j for j in jobs if not sigs[j.source_key].forced]

    def call(model, prompt):
        return json.dumps({"keep": [1]})       # 첫 건만 남긴다

    kept, dropped = prescreen.prescreen(jobs, sigs, call=call, log=lambda *a: None)
    assert [j.source_key for j in kept] == [survivors[0].source_key]
    assert len(kept) + len(dropped) == len(jobs)


def test_out_of_range_numbers_are_ignored():
    jobs = _jobs()
    sigs = signals.analyze_all(jobs)

    def call(model, prompt):
        return json.dumps({"keep": [0, 1, 999, -3]})

    kept, _ = prescreen.prescreen(jobs, sigs, call=call, log=lambda *a: None)
    assert len(kept) == 1


def test_prompt_carries_titles_only():
    """이 단계의 요점은 '적게 준다'는 것 — 공고 본문이 새어 들어가면 안 된다."""
    job = JobPosting(source="mock", source_id="1", title="시스템 엔지니어",
                     company="OO테크", url="", role="sys_ops",
                     raw_text="여기에는 아주 긴 공고 본문이 들어있다 " * 50)
    prompt = prescreen.build_prompt([job])
    assert "시스템 엔지니어" in prompt and "OO테크" in prompt
    assert "긴 공고 본문" not in prompt
    assert len(prompt) < 300


def test_no_llm_mode_drops_forced_only():
    jobs = _jobs()
    sigs = signals.analyze_all(jobs)
    kept, dropped = prescreen.prescreen(jobs, sigs, use_llm=False, log=lambda *a: None)
    assert len(kept) + len(dropped) == len(jobs)
    assert all(sigs[d.source_key].forced for d in dropped)


def test_publish_flow_never_publishes_a_screened_out_job():
    """프리스크린에서 버린 공고는 판정도 발행도 되지 않는다 — 이게 DB가 안 불어나는 이유다."""
    created, judged = [], []
    keep_one = _mock_jobs()[:1]

    def evaluate_fn(jobs):
        judged.extend(j.source_key for j in jobs)
        sigs = signals.analyze_all(jobs)
        from src import evaluator
        return sigs, evaluator.evaluate(jobs, sigs, use_llm=False)

    msg = _publish_flow(
        FakeState(), week="2026-W33",
        collect_fn=_mock_jobs, evaluate_fn=evaluate_fn,
        screen_fn=lambda jobs: keep_one,
        publisher=lambda job, v, sig, week: created.append(job.source_key),
        error_publisher=lambda m, w: None, log=lambda *a, **k: None,
    )
    assert created == [keep_one[0].source_key]
    assert judged == [keep_one[0].source_key]
    assert msg.startswith("[published]")


def test_publish_flow_skips_when_prescreen_keeps_nothing():
    created = []
    msg = _publish_flow(
        FakeState(), week="2026-W33",
        collect_fn=_mock_jobs, evaluate_fn=lambda jobs: ({}, {}),
        screen_fn=lambda jobs: [],
        publisher=lambda job, v, sig, week: created.append(job.source_key),
        error_publisher=lambda m, w: None, log=lambda *a, **k: None,
    )
    assert created == [] and "프리스크린 통과 0건" in msg
