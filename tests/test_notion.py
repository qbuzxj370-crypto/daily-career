"""노션 발행 계약 검증 — 실제 API 없이 클라이언트를 가짜로 주입해 payload만 본다."""
from __future__ import annotations
import json

from src import evaluator, notion_pub, signals
from src.pipeline import _mock_jobs
from src.renderer import to_markdown


class FakeClient:
    def __init__(self):
        self.created = []
        self.pages = self
        self.databases = self

    def create(self, **kw):
        self.created.append(kw)
        return {"id": "page-1", "url": "https://notion.so/page-1"}

    def retrieve(self, database_id):
        return {"data_sources": [{"id": "ds-1"}]}


def _publish_one(index: int = 1):
    job = _mock_jobs()[index]
    sig = signals.analyze_job(job)
    v = evaluator.evaluate([job], {job.source_key: sig}, use_llm=False)[job.source_key]
    client = FakeClient()
    notion_pub.publish(job, v, sig, "2026-W33", client=client, db_id="db-1")
    return job, v, sig, client.created[0]


def test_source_key_is_a_property_not_body_text():
    """속성만 쿼리 가능하다 — SourceKey가 본문에만 있으면 중복 제거가 불가능해진다."""
    job, _v, _sig, payload = _publish_one()
    props = payload["properties"]
    assert props["SourceKey"]["rich_text"][0]["text"]["content"] == job.source_key
    assert props["CollectedWeek"]["rich_text"][0]["text"]["content"] == "2026-W33"


def test_status_is_created_as_new_only():
    """Status는 사람이 관리하는 열 — 생성 시 '신규'만 넣는다(갱신 경로 없음)."""
    _job, _v, _sig, payload = _publish_one()
    assert payload["properties"]["Status"]["select"]["name"] == "신규"
    assert payload["properties"]["Kind"]["select"]["name"] == "job"
    # notion_pub에 페이지 수정 API(update)가 아예 없어야 한다.
    assert not [n for n in dir(notion_pub) if "update" in n.lower()]


def test_forced_risk_reaches_notion_properties():
    job, v, _sig, payload = _publish_one(1)   # 픽스처 1002 = 24*365 / 4조 3교대
    assert v.verdict == "위험"
    assert payload["properties"]["Verdict"]["select"]["name"] == "위험"
    names = {o["name"] for o in payload["properties"]["Signals"]["multi_select"]}
    assert "관제 전담" in names


def test_multi_select_rejects_unknown_signal_names():
    """LLM이 지어낸 신호명이 노션 옵션을 오염시키지 않는다."""
    v = evaluator.Verdict(source_key="mock:1", risk_signals=["관제 전담", "야근 많음"],
                          positive_signals=["구축 조직 동거", "복지 좋음"])
    assert notion_pub._signal_names(v) == ["관제 전담", "구축 조직 동거"]


def test_body_has_no_markdown_table():
    """md_to_notion.py는 표를 파싱하지 못한다 — 렌더 결과에 표가 있으면 안 된다."""
    job, v, sig, payload = _publish_one()
    md = to_markdown(job, v, sig, "2026-W33")
    assert "|" not in md
    assert payload["children"] and payload["children"][0]["type"] == "heading_1"


def test_error_page_shape():
    client = FakeClient()
    notion_pub.publish_error("CollectError: 사람인 API 다운", "2026-W33",
                             client=client, db_id="db-1")
    payload = client.created[0]
    assert payload["properties"]["Kind"]["select"]["name"] == "error"
    assert "[ERROR]" in json.dumps(payload["properties"], ensure_ascii=False)


def test_schema_has_every_property_publish_writes():
    """init-db 스키마에 없는 속성에 쓰면 Notion이 400을 낸다."""
    _job, _v, _sig, payload = _publish_one(0)   # URL/Deadline/Location/Experience 모두 있는 건
    schema = set(notion_pub._schema_properties())
    assert set(payload["properties"]) <= schema
    assert {"URL", "Deadline", "Location", "Experience"} <= set(payload["properties"])
