"""상태 정본 인터페이스. 정본 = Notion DB (로컬 상태 파일 없음 — 러너는 ephemeral).

두 개의 **독립된** 멱등 레이어를 파생시킨다. 하나로 합치지 말 것:
  1) week_exists(week)     — 이번 ISO 주차에 이미 수집했는가 (같은 주 재실행 시 전부 스킵)
  2) known_source_keys()   — 이전 주차에 이미 본 공고인가 (같은 공고 재생성 방지)
둘 중 하나를 제거하면 나머지가 우회되는 순간 중복 페이지가 생긴다.
"""
from __future__ import annotations
import re
from abc import ABC, abstractmethod
from datetime import date, datetime, timedelta

from config import settings

_WEEK_RE = re.compile(r"^\d{4}-W\d{2}$")


class State(ABC):
    @abstractmethod
    def week_exists(self, week: str) -> bool: ...
    @abstractmethod
    def known_source_keys(self) -> set[str]: ...

    def purge_before(self, cutoff_week: str, *, log=print) -> tuple[int, int]:
        """오래된 주차 페이지 정리. 기본은 아무것도 하지 않는다(노션 미연동 상태 구현용)."""
        return (0, 0)


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

    @staticmethod
    def _select(page: dict, prop: str) -> str:
        sel = page.get("properties", {}).get(prop, {}).get("select") or {}
        return sel.get("name", "")

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

    def purge_before(self, cutoff_week: str, *, log=print) -> tuple[int, int]:
        """`cutoff_week`보다 오래된 주차의 페이지를 보관(archive)한다. (정리, 보존) 반환.

        **`Status`가 '신규'가 아닌 페이지는 절대 건드리지 않는다.** Status는 사람이 손으로
        관리하는 열이고(검토중/지원/보류/탈락), 지원까지 한 공고를 나이 때문에 지워버리면
        사용자의 분류 작업이 사라진다. 나이는 정리의 필요조건일 뿐 충분조건이 아니다.

        발행이 create-only인 것과 모순되지 않는다 — 저쪽은 "판정 결과로 기존 페이지를
        덮어쓰지 않는다"는 뜻이고, 이건 사용자가 명시적으로 요청한 별도의 정리 동작이다.

        비교는 `YYYY-Www` 문자열의 사전순으로 한다(0 패딩이라 시간순과 일치).
        CollectedWeek이 비었거나 형식이 다르면 판단하지 않고 남긴다.
        """
        try:
            pages = self._query({"property": "Kind", "select": {"equals": "job"}})
            pages += self._query({"property": "Kind", "select": {"equals": "error"}})
        except Exception as e:  # noqa: BLE001
            if _is_missing_select_option(e):
                return (0, 0)
            raise

        purged = held = 0
        for page in pages:
            week = self._rich_text(page, "CollectedWeek").strip()
            if not _WEEK_RE.match(week) or week >= cutoff_week:
                continue
            status = self._select(page, "Status")
            if status and status != "신규":
                held += 1
                continue
            self.client.pages.update(page_id=page["id"], archived=True)
            purged += 1
        if purged or held:
            log(f"  정리: {cutoff_week} 이전 {purged}건 보관"
                + (f" / 손댄 흔적이 있어 남긴 것 {held}건(Status≠신규)" if held else ""))
        return (purged, held)


def iso_week(now: datetime | None = None) -> str:
    """ISO 주차 문자열 `2026-W33` (KST 기준)."""
    now = now or datetime.now(settings.TIMEZONE)
    y, w, _ = now.isocalendar()
    return f"{y}-W{w:02d}"


def week_minus(week: str, n: int) -> str:
    """`2026-W33`에서 n주 전 주차 문자열. 연도 경계를 ISO 달력으로 넘긴다."""
    y, w = int(week[:4]), int(week[6:])
    monday = date.fromisocalendar(y, w, 1) - timedelta(weeks=n)
    yy, ww, _ = monday.isocalendar()
    return f"{yy}-W{ww:02d}"


def purge_cutoff(week: str, keep_weeks: int | None = None) -> str:
    """이 주차 문자열 **미만**을 정리 대상으로 삼는다.

    keep_weeks=3, week=2026-W33 → 2026-W31 (즉 W31·W32·W33을 남기고 W30 이하를 정리).
    """
    keep = keep_weeks if keep_weeks is not None else settings.PURGE_KEEP_WEEKS
    return week_minus(week, max(keep - 1, 0))


def today_kst() -> str:
    return datetime.now(settings.TIMEZONE).strftime("%Y-%m-%d")
