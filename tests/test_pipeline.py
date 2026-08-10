"""오케스트레이션 검증 — 실제 API 없이 주입으로만 돌린다(_publish_flow의 존재 이유).

두 멱등 레이어(주차 / SourceKey)가 **각각** 동작하는지, 슬랙이 위험 공고 제목을 싣지
않는지, 배치 실패가 개별 호출로 격하되는지를 본다.
"""
from __future__ import annotations
import json

import pytest

from src import evaluator, signals
from src.evaluator import Verdict
from src.pipeline import MockSource, _mock_jobs, _publish_flow
from src.renderer import to_slack_blocks
from src.state import State, iso_week


class FakeState(State):
    def __init__(self, week=None, keys=()):
        self._week, self._keys = week, set(keys)

    def week_exists(self, week: str) -> bool:
        return week == self._week

    def known_source_keys(self) -> set[str]:
        return set(self._keys)


def _evaluate_fn(jobs):
    sigs = signals.analyze_all(jobs)
    return sigs, evaluator.evaluate(jobs, sigs, use_llm=False)


def _flow(state, **kw):
    created = []
    errors = []
    slack = []
    defaults = dict(
        week="2026-W33",
        collect_fn=lambda: _mock_jobs(),
        evaluate_fn=_evaluate_fn,
        publisher=lambda job, v, sig, week: created.append(job.source_key),
        error_publisher=lambda msg, week: errors.append(msg),
        slack_fn=lambda week, results, url: slack.append(results),
        log=lambda *a, **k: None,
    )
    defaults.update(kw)
    msg = _publish_flow(state, **defaults)
    return msg, created, errors, slack


def test_week_idempotency_skips_everything():
    msg, created, errors, slack = _flow(FakeState(week="2026-W33"))
    assert msg.startswith("[skip]") and created == [] and slack == []


def test_source_key_dedup_is_independent_of_week():
    """주차가 달라도 이미 본 SourceKey는 다시 만들지 않는다(멱등 레이어 2)."""
    known = {j.source_key for j in _mock_jobs()[:3]}
    msg, created, _errors, _slack = _flow(FakeState(week="2026-W32", keys=known))
    assert set(created) == {j.source_key for j in _mock_jobs()[3:]}
    assert "[published]" in msg


def test_all_new_published_and_slack_called():
    msg, created, errors, slack = _flow(FakeState())
    assert len(created) == len(_mock_jobs())
    assert errors == [] and len(slack) == 1
    assert "[published]" in msg


def test_collect_failure_writes_error_page_and_raises():
    def boom():
        raise RuntimeError("사람인 API 다운")
    with pytest.raises(RuntimeError):
        _flow(FakeState(), collect_fn=boom)


def test_page_failure_does_not_stop_the_rest():
    created = []

    def publisher(job, v, sig, week):
        if job.source_id == "1002":
            raise RuntimeError("notion 400")
        created.append(job.source_key)

    with pytest.raises(RuntimeError):
        _flow(FakeState(), publisher=publisher)
    # 실패 1건을 제외한 나머지는 발행됐다(부분 발행 후 error 페이지 + 예외).
    assert len(created) == len(_mock_jobs()) - 1


def test_slack_failure_does_not_invalidate_publish():
    def boom(week, results, url):
        raise RuntimeError("webhook 500")
    msg, created, errors, _slack = _flow(FakeState(), slack_fn=boom)
    assert "[published]" in msg and errors == [] and created


# ---------------------------------------------------------------- 슬랙 불변식

def test_slack_never_lists_risky_titles():
    jobs = _mock_jobs()
    sigs = signals.analyze_all(jobs)
    verdicts = evaluator.evaluate(jobs, sigs, use_llm=False)
    results = [(j, verdicts[j.source_key]) for j in jobs]
    risky = [j for j, v in results if v.verdict == "위험"]
    assert risky, "픽스처에 위험 공고가 있어야 이 테스트가 의미 있다"

    blob = json.dumps(to_slack_blocks("2026-W33", results, "https://notion.so/x"),
                      ensure_ascii=False)
    for job in risky:
        assert job.title not in blob
    assert f"🔴 위험 {len(risky)}" in blob


# ---------------------------------------------------------------- 배치 판정

def test_batch_failure_falls_back_to_individual_calls():
    jobs = _mock_jobs()
    sigs = signals.analyze_all(jobs)
    calls = []

    def call(model, prompt):
        n = prompt.count("- source_key:")
        calls.append(n)
        if n > 1:
            raise RuntimeError("RPM 초과")   # 배치는 항상 실패
        key = prompt.split("- source_key: ")[1].split("\n")[0].strip()
        return json.dumps([{"source_key": key, "verdict": "적합", "score": 5,
                            "summary": "s", "reason": "r"}])

    out = evaluator.evaluate(jobs, sigs, call=call, batch_size=3)
    assert len(out) == len(jobs)
    assert max(calls) > 1 and calls.count(1) >= len(jobs)
    # 규칙이 확정한 위험 건은 LLM이 '적합'을 줘도 위험으로 되돌아온다.
    forced = [k for k, s in sigs.items() if s.forced]
    assert forced and all(out[k].verdict == "위험" for k in forced)


def test_llm_total_failure_falls_back_to_rule_verdict():
    jobs = _mock_jobs()[:2]
    sigs = signals.analyze_all(jobs)

    def call(model, prompt):
        raise RuntimeError("죽음")

    out = evaluator.evaluate(jobs, sigs, call=call, batch_size=10)
    assert len(out) == 2
    assert all(v.by_rule and v.llm_failed for v in out.values())


def test_mock_source_feeds_collector():
    src = MockSource()
    got = src.fetch("클라우드", "cloud", 10)
    assert got and all(j.role == "cloud" for j in got)


def test_iso_week_format():
    assert len(iso_week()) == 8 and iso_week()[4:6] == "-W"
