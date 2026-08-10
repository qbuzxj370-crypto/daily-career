"""NotionState 검증 — 가짜 Notion 클라이언트로 쿼리 횟수/페이지네이션까지 본다."""
from __future__ import annotations

from src.state import NotionState, iso_week


def _page(source_key: str) -> dict:
    return {"properties": {"SourceKey": {"rich_text": [{"plain_text": source_key}]}}}


class FakeDataSources:
    def __init__(self, pages: list[dict], page_size_cap: int = 100):
        self.pages = pages
        self.cap = page_size_cap
        self.calls: list[dict] = []

    def query(self, **kw):
        self.calls.append(kw)
        start = int(kw.get("start_cursor") or 0)
        size = min(kw.get("page_size", 100), self.cap)
        chunk = self.pages[start:start + size]
        nxt = start + size
        return {"results": chunk, "has_more": nxt < len(self.pages), "next_cursor": str(nxt)}


class FakeClient:
    def __init__(self, pages):
        self.data_sources = FakeDataSources(pages)


def test_known_source_keys_paginates_in_one_sweep():
    """공고당 1쿼리는 금물 — 전량을 페이지 단위로만 훑는다."""
    pages = [_page(f"saramin:{i}") for i in range(250)]
    client = FakeClient(pages)
    st = NotionState(client=client, db_id="db", ds_id="ds")
    keys = st.known_source_keys()
    assert len(keys) == 250 and "saramin:0" in keys
    assert len(client.data_sources.calls) == 3      # 100 + 100 + 50
    assert all(c["page_size"] == 100 for c in client.data_sources.calls)


def test_week_exists_stops_at_first_hit():
    """멱등 확인은 1건만 보면 된다 — limit으로 조기 종료(전량 훑지 않음)."""
    client = FakeClient([_page(f"s:{i}") for i in range(500)])
    st = NotionState(client=client, db_id="db", ds_id="ds")
    assert st.week_exists("2026-W33") is True
    assert len(client.data_sources.calls) == 1


def test_week_exists_false_on_empty():
    client = FakeClient([])
    st = NotionState(client=client, db_id="db", ds_id="ds")
    assert st.week_exists("2026-W33") is False


def test_filters_target_kind_job():
    client = FakeClient([])
    st = NotionState(client=client, db_id="db", ds_id="ds")
    st.week_exists("2026-W33")
    st.known_source_keys()
    blob = str(client.data_sources.calls)
    assert "CollectedWeek" in blob and "2026-W33" in blob
    assert blob.count("'job'") >= 2


def test_iso_week_matches_python_isocalendar():
    from datetime import datetime
    from config import settings
    now = datetime(2026, 8, 14, tzinfo=settings.TIMEZONE)     # 금요일
    assert iso_week(now) == "2026-W33"
