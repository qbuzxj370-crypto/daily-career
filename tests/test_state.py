"""NotionState 검증 — 가짜 Notion 클라이언트로 쿼리 횟수/페이지네이션까지 본다."""
from __future__ import annotations

import pytest

from src.state import NotionState, iso_week, purge_cutoff, week_minus


def _page(source_key: str) -> dict:
    return {"properties": {"SourceKey": {"rich_text": [{"plain_text": source_key}]}}}


def _job_page(page_id: str, week: str, status: str = "신규") -> dict:
    return {"id": page_id, "properties": {
        "SourceKey": {"rich_text": [{"plain_text": f"worknet:{page_id}"}]},
        "CollectedWeek": {"rich_text": [{"plain_text": week}]},
        "Status": {"select": {"name": status} if status else None},
    }}


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


class PurgePages:
    """`client.pages.update`만 흉내낸다 — 보관 처리는 이 호출로만 일어나야 한다."""
    def __init__(self):
        self.updates: list[dict] = []

    def update(self, **kw):
        self.updates.append(kw)
        return {"id": kw.get("page_id"), "archived": kw.get("archived")}


class PurgeClient:
    """Kind별로 다른 결과를 주는 가짜 — purge_before는 job/error를 따로 훑는다."""
    def __init__(self, by_kind: dict[str, list[dict]]):
        self.pages = PurgePages()
        outer = self

        class DS:
            def query(self, **kw):
                kind = kw["filter"]["select"]["equals"]
                return {"results": outer.by_kind.get(kind, []), "has_more": False}

        self.by_kind = by_kind
        self.data_sources = DS()


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


# --- 보관 기간 정리 -----------------------------------------------------------

def _purge(pages, cutoff="2026-W31"):
    client = PurgeClient({"job": pages, "error": []})
    st = NotionState(client=client, db_id="db", ds_id="ds")
    result = st.purge_before(cutoff, log=lambda *a: None)
    archived = {u["page_id"] for u in client.pages.updates}
    return result, archived, client


def test_purge_archives_only_weeks_before_the_cutoff():
    (purged, held), archived, _ = _purge([
        _job_page("old", "2026-W29"),
        _job_page("edge", "2026-W30"),
        _job_page("keep", "2026-W31"),      # cutoff와 같은 주차는 남는다
        _job_page("now", "2026-W33"),
    ])
    assert archived == {"old", "edge"}
    assert (purged, held) == (2, 0)


def test_purge_never_touches_a_page_the_user_triaged():
    """Status는 사람이 관리하는 열이다. 지원까지 한 공고를 나이 때문에 지우면 안 된다."""
    (purged, held), archived, _ = _purge([
        _job_page("applied", "2026-W20", status="지원"),
        _job_page("review", "2026-W20", status="검토중"),
        _job_page("fresh", "2026-W20", status="신규"),
    ])
    assert archived == {"fresh"}
    assert (purged, held) == (1, 2)


def test_purge_leaves_pages_with_unreadable_week():
    """주차를 못 읽으면 나이를 알 수 없다 — 모르면 지우지 않는다."""
    (purged, _held), archived, _ = _purge([
        _job_page("blank", ""),
        _job_page("weird", "지난주"),
    ])
    assert archived == set() and purged == 0


def test_purge_archives_via_update_not_delete():
    _r, _a, client = _purge([_job_page("old", "2026-W10")])
    assert client.pages.updates == [{"page_id": "old", "archived": True}]


@pytest.mark.parametrize("week,n,want", [
    ("2026-W33", 0, "2026-W33"),
    ("2026-W33", 2, "2026-W31"),
    ("2026-W02", 3, "2025-W51"),      # 연도 경계를 ISO 달력으로 넘는다
])
def test_week_minus(week, n, want):
    assert week_minus(week, n) == want


def test_purge_cutoff_keeps_n_weeks_including_this_one():
    # 3주 보관 = W31·W32·W33을 남기고 W30 이하를 정리
    assert purge_cutoff("2026-W33", 3) == "2026-W31"
    assert purge_cutoff("2026-W33", 1) == "2026-W33"


def test_week_strings_compare_chronologically():
    """정리 판정이 문자열 비교에 의존하므로 사전순 == 시간순임을 못 박아 둔다."""
    assert "2026-W02" < "2026-W10" < "2026-W33" < "2027-W01"


def test_iso_week_matches_python_isocalendar():
    from datetime import datetime
    from config import settings
    now = datetime(2026, 8, 14, tzinfo=settings.TIMEZONE)     # 금요일
    assert iso_week(now) == "2026-W33"
