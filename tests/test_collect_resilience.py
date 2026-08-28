"""연결 실패 처리 — 재시도 · 간격 · 소스 차단, 그리고 '구조 문제와 구분'.

2026-08-28 CI 실행에서 워크넷이 `ConnectTimeout`으로 죽었는데, 안내 문구가 이걸
페이지 구조 변경으로 몰아서 멀쩡한 페이지를 뜯어보는 데 시간이 샜다. 여기서 지키는
불변식은 두 가지다:

  1. **응답을 못 받은 것**(`SourceUnreachable`)과 **200을 받았는데 내용이 달라진 것**
     (`SourceError`)은 타입부터 다르다. 상위 안내가 이 둘을 섞으면 안 된다.
  2. 연결 실패는 재시도하되 **무한정 태우지 않는다.** IP가 막힌 상황에서 27개 키워드를
     타임아웃마다 수십 초씩 기다리면 러너 예산이 먼저 죽는다.

네트워크는 한 번도 타지 않는다 — httpx.get과 sleep을 전부 주입/대체한다.
"""
from __future__ import annotations

import httpx
import pytest

from config import settings
from src import collector
from src.sources import worknet
from src.sources.base import (
    JobPosting, SourceError, SourceUnreachable, http_get,
)


class FakeResponse:
    def __init__(self, status_code: int, text: str = "", ctype: str = "text/html"):
        self.status_code = status_code
        self.text = text
        self.headers = {"content-type": ctype}


def _patch_get(monkeypatch, outcomes):
    """httpx.get을 outcomes 순서대로 소비하는 스텁으로 교체. 호출 기록을 돌려준다."""
    calls: list[dict] = []
    seq = list(outcomes)

    def fake_get(url, **kwargs):
        calls.append({"url": url, "timeout": kwargs.get("timeout")})
        item = seq.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(httpx, "get", fake_get)
    return calls


def _no_sleep():
    slept: list[float] = []
    return slept, slept.append


# --------------------------------------------------------------- http_get 재시도

def test_transport_error_is_retried_then_succeeds(monkeypatch):
    """첫 시도가 ConnectTimeout이어도 두 번째가 붙으면 성공으로 끝난다."""
    calls = _patch_get(monkeypatch, [
        httpx.ConnectTimeout("timed out"),
        FakeResponse(200, "<html>ok</html>"),
    ])
    slept, sleep = _no_sleep()

    ctype, body = http_get("https://example.test", {}, timeout=5,
                           retries=2, backoff=1.0, sleep=sleep)

    assert body == "<html>ok</html>"
    assert len(calls) == 2
    assert slept and slept[0] >= 1.0   # 백오프가 실제로 걸렸다(지터로 조금 더 클 수 있다)


def test_exhausted_retries_raise_unreachable_not_structure_error(monkeypatch):
    """끝내 못 닿으면 `SourceUnreachable` — 구조 문제와 **타입이 다르다.**

    이게 이 파일의 핵심이다. 문구가 아니라 타입으로 갈려야 상위 안내가 안 섞인다.
    """
    calls = _patch_get(monkeypatch, [httpx.ConnectTimeout("timed out")] * 3)
    _slept, sleep = _no_sleep()

    with pytest.raises(SourceUnreachable) as exc:
        http_get("https://example.test", {}, timeout=5,
                 retries=2, backoff=0.01, sleep=sleep)

    assert len(calls) == 3                       # 최초 1 + 재시도 2
    assert "페이지 구조와는 무관" in str(exc.value)
    assert isinstance(exc.value, SourceError)    # 상위 except SourceError는 계속 잡는다


def test_client_error_is_not_retried(monkeypatch):
    """4xx는 다시 보내도 같은 답이다 — 재시도하면 시간만 버린다."""
    calls = _patch_get(monkeypatch, [FakeResponse(404, "no such page")])
    _slept, sleep = _no_sleep()

    with pytest.raises(SourceError) as exc:
        http_get("https://example.test", {}, timeout=5,
                 retries=2, backoff=0.01, sleep=sleep)

    assert len(calls) == 1
    assert not isinstance(exc.value, SourceUnreachable)   # 닿긴 닿았다


def test_rate_limit_is_retried_but_is_not_unreachable(monkeypatch):
    """429는 재시도하되, 실패해도 '못 닿음'이 아니다 — 서버는 살아서 거절한 것이다."""
    calls = _patch_get(monkeypatch, [FakeResponse(429)] * 3)
    _slept, sleep = _no_sleep()

    with pytest.raises(SourceError) as exc:
        http_get("https://example.test", {}, timeout=5,
                 retries=2, backoff=0.01, sleep=sleep)

    assert len(calls) == 3
    assert not isinstance(exc.value, SourceUnreachable)
    assert "거절" in str(exc.value)


def test_connect_timeout_is_capped_below_total_timeout(monkeypatch):
    """연결 타임아웃은 전체 타임아웃과 따로, 더 짧게 걸린다.

    SYN이 드롭되는 상황에서 요청 하나가 전체 타임아웃만큼 서 있으면 27개 키워드를
    도는 동안 러너의 20분이 사라진다.
    """
    calls = _patch_get(monkeypatch, [FakeResponse(200, "ok")])
    monkeypatch.setattr(settings, "HTTP_CONNECT_TIMEOUT", 8.0)

    http_get("https://example.test", {}, timeout=30, retries=0)

    timeout = calls[0]["timeout"]
    assert timeout.connect == 8.0
    assert timeout.read == 30


# --------------------------------------------------------------- 워크넷 요청 간격

def test_worknet_throttle_skips_the_first_request_then_waits(monkeypatch):
    """첫 요청은 기다리지 않고, 두 번째부터 간격이 걸린다."""
    monkeypatch.setattr(settings, "WORKNET_REQUEST_GAP", 1.0)
    monkeypatch.setattr(worknet, "_last_request", 0.0)
    slept, sleep = _no_sleep()

    assert worknet._throttle(sleep=sleep) == 0.0   # 첫 호출 — 기다릴 이유가 없다
    waited = worknet._throttle(sleep=sleep)

    assert 0 < waited <= 1.0
    assert slept == [waited]


def test_worknet_throttle_disabled_by_zero_gap(monkeypatch):
    """간격 0이면 완전히 꺼진다(로컬 반복 실행·디버깅용)."""
    monkeypatch.setattr(settings, "WORKNET_REQUEST_GAP", 0.0)
    monkeypatch.setattr(worknet, "_last_request", 0.0)
    slept, sleep = _no_sleep()

    worknet._throttle(sleep=sleep)
    worknet._throttle(sleep=sleep)

    assert slept == []


# --------------------------------------------------------- collector 소스 차단

class FlakySource:
    """지정한 결과를 순서대로 내는 가짜 소스. 남으면 마지막 결과를 반복한다."""

    def __init__(self, name: str, outcomes):
        self.name = name
        self._outcomes = list(outcomes)
        self.calls = 0

    def fetch(self, keyword: str, role: str, limit: int) -> list[JobPosting]:
        self.calls += 1
        item = self._outcomes[min(self.calls - 1, len(self._outcomes) - 1)]
        if isinstance(item, Exception):
            raise item
        return [JobPosting(source=self.name, source_id=f"{keyword}-{self.calls}",
                           title="시스템 엔지니어", company="테스트", url="",
                           role=role)]

    def probe(self, keyword: str):   # pragma: no cover - 프로토콜 충족용
        return "json", "{}"


def _unreachable():
    return SourceUnreachable("응답을 받지 못했습니다: 3회 시도 모두 실패")


def test_source_is_disabled_after_consecutive_connection_failures(monkeypatch):
    """연속 N회 못 닿으면 그 소스는 이번 실행에서 접는다.

    IP가 막혔다면 남은 키워드도 확실히 같은 결과다. 키 미설정으로 소스를 끄는 것과
    같은 논리이고, 여기서 안 끊으면 타임아웃 × 남은 키워드만큼 예산이 샌다.
    """
    monkeypatch.setattr(settings, "SOURCE_UNREACHABLE_LIMIT", 3)
    src = FlakySource("worknet", [_unreachable()])

    with pytest.raises(collector.CollectError) as exc:
        collector.collect([src], role_slugs=["cloud"], log=lambda *_: None)

    assert src.calls == 3          # cloud 키워드는 5개지만 3회에서 멈춘다
    assert exc.value.unreachable   # 원인이 연결이라는 사실이 상위까지 전달된다


def test_one_success_resets_the_streak(monkeypatch):
    """'연속'이 조건이다 — 중간에 한 번 닿으면 소스를 끊지 않는다.

    드문드문 나는 실패까지 차단으로 처리하면, 잠깐 흔들린 주에 소스 하나가 통째로
    사라진 채 발행된다.
    """
    monkeypatch.setattr(settings, "SOURCE_UNREACHABLE_LIMIT", 3)
    src = FlakySource("worknet", [_unreachable(), _unreachable(), "ok",
                                  _unreachable(), _unreachable()])

    jobs = collector.collect([src], role_slugs=["cloud"], log=lambda *_: None)

    assert src.calls == 5          # 5개 키워드를 끝까지 돈다
    assert len(jobs) == 1


def test_parse_failure_is_not_reported_as_a_connection_problem(monkeypatch):
    """구조 실패만 있었으면 `unreachable`은 False다.

    이 플래그가 `--probe` 안내를 낼지 말지를 가른다(src/pipeline.py).
    """
    monkeypatch.setattr(settings, "SOURCE_UNREACHABLE_LIMIT", 3)
    src = FlakySource("worknet", [SourceError("워크넷 목록 행을 분할하지 못했습니다")])

    with pytest.raises(collector.CollectError) as exc:
        collector.collect([src], role_slugs=["cloud"], log=lambda *_: None)

    assert not exc.value.unreachable
    assert src.calls == 5          # 구조 실패는 소스를 끄지 않는다(키워드마다 다를 수 있다)
