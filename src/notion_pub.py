"""Notion DB 페이지 생성 (공고 1건 = 페이지 1개). 정본 = Notion DB.

**발행은 create-only다.** 기존 페이지를 절대 수정하지 않는다 — `Status`는 사람이 손으로
관리하는 열이라(신규/검토중/지원/보류/탈락) "갱신·동기화" 기능을 붙이면 사용자의 분류
작업을 조용히 지워버린다. 파이프라인은 생성 시 `신규`만 넣는다.
"""
from __future__ import annotations

from config import rules, roles, settings
from src.evaluator import Verdict, VERDICTS
from src.renderer import page_title, source_display, to_markdown, to_notion_blocks
from src.signals import SignalResult
from src.sources.base import JobPosting
from src.state import resolve_data_source_id

STATUSES = ["신규", "검토중", "지원", "보류", "탈락"]
SOURCES = ["사람인", "워크넷"]


def _client():
    from notion_client import Client  # 지연 import
    return Client(auth=settings.NOTION_API_KEY)


def _ds_id(client, db_id: str) -> str:
    return settings.NOTION_DATA_SOURCE_ID or resolve_data_source_id(client, db_id)


def _txt(value: str | None, limit: int = 2000) -> dict:
    return {"rich_text": [{"text": {"content": (value or "")[:limit]}}]}


def _props(job: JobPosting, v: Verdict, sig: SignalResult, week: str) -> dict:
    props: dict = {
        "Title": {"title": [{"text": {"content": page_title(job)[:2000]}}]},
        "Company": _txt(job.company),
        "Verdict": {"select": {"name": v.verdict}},
        "Score": {"number": v.score},
        "Role": {"select": {"name": roles.display_name(job.role)}},
        "Signals": {"multi_select": [{"name": s} for s in _signal_names(v)]},
        "Source": {"select": {"name": source_display(job.source)}},
        # 중복 제거 키 — 반드시 **속성**에 저장한다(본문은 쿼리할 수 없다).
        "SourceKey": _txt(job.source_key),
        "CollectedWeek": _txt(week),
        "Status": {"select": {"name": "신규"}},
        "Kind": {"select": {"name": "job"}},
    }
    if job.url:
        props["URL"] = {"url": job.url}
    if job.deadline:
        props["Deadline"] = {"date": {"start": job.deadline}}
    if job.location:
        props["Location"] = _txt(job.location)
    if job.experience:
        props["Experience"] = _txt(job.experience)
    return props


def _signal_names(v: Verdict) -> list[str]:
    """multi_select 옵션은 정본(config/rules.py) 이름만 허용 — LLM이 지어낸 이름은 버린다."""
    known = set(rules.ALL_SIGNAL_NAMES)
    out, seen = [], set()
    for s in v.risk_signals + v.positive_signals:
        if s in known and s not in seen:
            out.append(s)
            seen.add(s)
    return out


def publish(job: JobPosting, v: Verdict, sig: SignalResult, week: str,
            client=None, db_id: str | None = None) -> dict:
    """공고 페이지 1개 생성. 생성된 page 객체(dict) 반환."""
    client = client or _client()
    db_id = db_id or settings.NOTION_DB_ID
    md = to_markdown(job, v, sig, week)
    return client.pages.create(
        parent={"type": "data_source_id", "data_source_id": _ds_id(client, db_id)},
        properties=_props(job, v, sig, week),
        children=to_notion_blocks(md),
    )


def publish_error(message: str, week: str, client=None, db_id: str | None = None) -> dict:
    """실패 알림 페이지(Kind=error)."""
    client = client or _client()
    db_id = db_id or settings.NOTION_DB_ID
    return client.pages.create(
        parent={"type": "data_source_id", "data_source_id": _ds_id(client, db_id)},
        properties={
            "Title": {"title": [{"text": {"content": f"[ERROR] {week}"}}]},
            "Kind": {"select": {"name": "error"}},
            "CollectedWeek": _txt(week),
            "Company": _txt(message),
        },
        children=[{
            "object": "block", "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": message[:2000]}}]},
        }],
    )


def _schema_properties() -> dict:
    """init-db용 DB 속성 스키마. select 옵션은 미리 채워 둔다."""
    return {
        "Title": {"title": {}},
        "Company": {"rich_text": {}},
        "Verdict": {"select": {"options": [{"name": v} for v in VERDICTS]}},
        "Score": {"number": {}},
        "Role": {"select": {"options": [{"name": roles.display_name(s)} for s in roles.SLUGS]}},
        "Signals": {"multi_select": {"options": [{"name": s} for s in rules.ALL_SIGNAL_NAMES]}},
        "Source": {"select": {"options": [{"name": s} for s in SOURCES]}},
        "SourceKey": {"rich_text": {}},
        "URL": {"url": {}},
        "Deadline": {"date": {}},
        "Location": {"rich_text": {}},
        "Experience": {"rich_text": {}},
        "CollectedWeek": {"rich_text": {}},
        "Status": {"select": {"options": [{"name": s} for s in STATUSES]}},
        "Kind": {"select": {"options": [{"name": "job"}, {"name": "error"}]}},
    }


def init_db(parent_page_id: str, client=None,
            title: str = "커리어 스카우트 (Career Scout)") -> dict:
    """부모 페이지 아래에 스키마대로 DB 생성(2025-09 API). daily-recall과 **별도 DB**.

    부모 페이지는 통합(NOTION_API_KEY)에 공유돼 있어야 한다.
    응답의 id=database id(=NOTION_DB_ID), data_sources[0].id=data source id.
    """
    client = client or _client()
    return client.databases.create(
        parent={"type": "page_id", "page_id": parent_page_id},
        title=[{"type": "text", "text": {"content": title}}],
        initial_data_source={"properties": _schema_properties()},
    )


def database_url(db_id: str | None = None) -> str:
    db_id = (db_id or settings.NOTION_DB_ID or "").replace("-", "")
    return f"https://www.notion.so/{db_id}" if db_id else ""
