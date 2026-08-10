"""상태 정본 인터페이스. 정본 = Notion DB (로컬 상태 파일 없음 — 러너는 ephemeral).

두 개의 **독립된** 멱등 레이어를 파생시킨다. 하나로 합치지 말 것:
  1) week_exists(week)     — 이번 ISO 주차에 이미 수집했는가 (같은 주 재실행 시 전부 스킵)
  2) known_source_keys()   — 이전 주차에 이미 본 공고인가 (같은 공고 재생성 방지)
둘 중 하나를 제거하면 나머지가 우회되는 순간 중복 페이지가 생긴다.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from datetime import datetime

from config import settings


class State(ABC):
    @abstractmethod
    def week_exists(self, week: str) -> bool: ...
    @abstractmethod
    def known_source_keys(self) -> set[str]: ...


class EmptyState(State):
    """콜드스타트: 이력 없음. --dry-run/--mock 전용(노션 미연동)."""
    def week_exists(self, week: str) -> bool:
        return False

    def known_source_keys(self) -> set[str]:
        return set()


def _is_missing_select_option(e: Exception) -> bool:
    """'select option ... not found for property' 검증 오류인지 판별.

    아직 어떤 select 옵션도 쓰이지 않은 값으로 필터할 때 Notion이 내는 400.
    이 특정 오류만 삼키고(=0건 취급) 다른 API 오류는 전파하기 위해 좁게 매칭한다.
    (daily-recall/src/state.py:38 그대로)
    """
    try:
        from notion_client.errors import APIResponseError
    except ImportError:
        return False
    return isinstance(e, APIResponseError) and "not found for property" in str(e)


def resolve_data_source_id(client, db_id: str) -> str:
    """Notion 2025-09 API: DB → data source id 해석(단일 소스 가정, 첫 소스 사용)."""
    db = client.databases.retrieve(database_id=db_id)
    sources = db.get("data_sources", [])
    if not sources:
        raise RuntimeError(f"DB {db_id}에 data source가 없습니다(Notion 2025-09 API).")
    return sources[0]["id"]


class NotionState(State):
    def __init__(self, client=None, db_id: str | None = None, ds_id: str | None = None):
        if client is None:
            from notion_client import Client
            client = Client(auth=settings.NOTION_API_KEY)
        self.client = client
        self.db_id = db_id or settings.NOTION_DB_ID
        self._ds_id = ds_id or settings.NOTION_DATA_SOURCE_ID or None

    def _data_source_id(self) -> str:
        if not self._ds_id:
            self._ds_id = resolve_data_source_id(self.client, self.db_id)
        return self._ds_id

    def _query(self, filt: dict, page_size: int = 100,
               limit: int | None = None, properties: list[str] | None = None) -> list[dict]:
        """페이지네이션 전량 조회(daily-recall/src/state.py:75). limit 지정 시 조기 종료."""
        results, cursor = [], None
        ds_id = self._data_source_id()
        while True:
            kw = {"data_source_id": ds_id, "filter": filt, "page_size": page_size}
            if cursor:
                kw["start_cursor"] = cursor
            resp = self.client.data_sources.query(**kw)
            results.extend(resp.get("results", []))
            if limit is not None and len(results) >= limit:
                return results[:limit]
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
        return results

    @staticmethod
    def _rich_text(page: dict, prop: str) -> str:
        rt = page.get("properties", {}).get(prop, {}).get("rich_text", [])
        return "".join(seg.get("plain_text", "") for seg in rt)

    def week_exists(self, week: str) -> bool:
        """이번 주차에 발행된 job 페이지가 1건이라도 있으면 True."""
        try:
            pages = self._query({"and": [
                {"property": "Kind", "select": {"equals": "job"}},
                {"property": "CollectedWeek", "rich_text": {"equals": week}},
            ]}, page_size=1, limit=1)
        except Exception as e:  # noqa: BLE001
            if _is_missing_select_option(e):
                return False
            raise
        return bool(pages)

    def known_source_keys(self) -> set[str]:
        """기존 SourceKey **전량**을 한 번에 조회(공고당 1쿼리 금지 — 수십 왕복이 된다)."""
        try:
            pages = self._query({"property": "Kind", "select": {"equals": "job"}})
        except Exception as e:  # noqa: BLE001
            if _is_missing_select_option(e):
                return set()
            raise
        return {k for k in (self._rich_text(p, "SourceKey") for p in pages) if k}


def iso_week(now: datetime | None = None) -> str:
    """ISO 주차 문자열 `2026-W33` (KST 기준)."""
    now = now or datetime.now(settings.TIMEZONE)
    y, w, _ = now.isocalendar()
    return f"{y}-W{w:02d}"


def today_kst() -> str:
    return datetime.now(settings.TIMEZONE).strftime("%Y-%m-%d")
