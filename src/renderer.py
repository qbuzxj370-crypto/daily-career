"""렌더러: (JobPosting + Verdict + SignalResult) -> 마크다운 / 노션 블록 / 슬랙 블록.

마크다운은 **통제된 부분집합**만 쓴다(H1~H3, 단락, **bold**, `code`, 불릿, ---, >).
md_to_notion.py가 그 부분집합만 파싱하므로 **표(`| ... |`)는 절대 쓰지 않는다.**
"""
from __future__ import annotations

from config import roles, settings
from src.evaluator import Verdict
from src.md_to_notion import to_blocks
from src.signals import SignalResult
from src.sources.base import JobPosting

VERDICT_EMOJI = {"적합": "🟢", "보통": "⚪", "위험": "🔴"}
SOURCE_DISPLAY = {"saramin": "사람인", "worknet": "워크넷"}

_SLACK_SECTION_LIMIT = 2900


def source_display(source: str) -> str:
    return SOURCE_DISPLAY.get(source, source)


def to_notion_blocks(markdown: str) -> list[dict]:
    return to_blocks(markdown)


def _clip(text: str) -> str:
    return text if len(text) <= _SLACK_SECTION_LIMIT else text[:_SLACK_SECTION_LIMIT] + "…"


def _signal_line(emoji: str, name: str, sig: SignalResult, extra: str = "") -> str:
    parts = [f"- {emoji} {name}"]
    if extra:
        parts.append(extra)
    hit = ", ".join(sig.hits.get(name, []))
    if hit:
        parts.append(f"— `{hit}`")
    return " ".join(parts)


def page_title(job: JobPosting) -> str:
    return f"{job.company} · {job.title}"


def to_markdown(job: JobPosting, v: Verdict, sig: SignalResult, week: str) -> str:
    emoji = VERDICT_EMOJI.get(v.verdict, "⚪")
    out: list[str] = [
        f"# {page_title(job)}",
        "",
        f"**판정:** {emoji} {v.verdict} ({v.score}/5)",
        f"**직무:** {roles.display_name(job.role)} · **소스:** {source_display(job.source)} "
        f"· **주차:** {week}",
        "",
    ]
    if v.summary:
        out += [f"> {v.summary}", ""]

    out += ["## 판정 근거", v.reason or "(근거 없음)", ""]

    out.append("## 신호")
    if not v.risk_signals and not v.positive_signals:
        out.append("- 규칙·판정에서 잡힌 신호 없음")
    for s in v.risk_signals:
        out.append(_signal_line("🔴", s, sig, extra="(완화됨)" if s in sig.mitigated else ""))
    for s in v.positive_signals:
        out.append(_signal_line("🟢", s, sig))
    out.append("")

    out.append("## 공고 정보")
    out.append(f"- 회사: {job.company}")
    out.append(f"- 회사 규모: {v.company_size}"
               + ("  ← 공고에 정보 없음. 노션에서 직접 확인 필요"
                  if v.company_size == "판단 불가" else ""))
    for label, value in (("지역", job.location), ("경력", job.experience),
                         ("고용형태", job.employment_type), ("마감", job.deadline)):
        if value:
            out.append(f"- {label}: {value}")
    if job.url:
        out.append(f"- 원문: {job.url}")
    out.append("")

    if sig.forced:
        out += ["---", "",
                f"> ⚠️ 규칙이 확정한 위험 신호로 판정을 **위험**으로 강제했습니다 — "
                f"{'; '.join(sig.forced_by)}"]
    if v.llm_failed:
        out += ["", "> ⚠️ LLM 판정에 실패해 규칙 판정으로 대체된 건입니다(요약 없음)."]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- 슬랙

def _line(job: JobPosting, v: Verdict) -> str:
    tags = " / ".join(v.positive_signals) or roles.display_name(job.role)
    title = f"<{job.url}|{job.title}>" if job.url else job.title
    return f"• [{job.company}] {title} — {tags}"


def to_slack_blocks(week: str, results: list[tuple[JobPosting, Verdict]],
                    db_url: str = "", *, top_n: int | None = None) -> list[dict]:
    """주간 요약 Block Kit.

    불변식: **위험 공고는 건수만 싣고 제목을 싣지 않는다.** 슬랙을 훑는 시간까지 아끼는 것이
    이 도구의 목적이며, 탈락 공고를 나열하면 없애려던 스캔을 그대로 재현하게 된다.
    (위험 공고 제목은 감사용으로 노션에 남아 있다.)
    """
    top_n = top_n or settings.SLACK_TOP_N
    fit = [(j, v) for j, v in results if v.verdict == "적합"]
    mid = [(j, v) for j, v in results if v.verdict == "보통"]
    risk = [(j, v) for j, v in results if v.verdict == "위험"]
    fit.sort(key=lambda t: (roles.sort_key(t[0].role), -t[1].score))

    blocks: list[dict] = [
        {"type": "header",
         "text": {"type": "plain_text", "text": f"📋 {week} 구직 스카우트", "emoji": True}},
        {"type": "section", "text": {"type": "mrkdwn", "text": _clip(
            f"신규 *{len(results)}건* · 🟢 적합 {len(fit)} · ⚪ 보통 {len(mid)} "
            f"· 🔴 위험 {len(risk)}")}},
    ]

    if fit:
        shown = fit[:top_n]
        text = "*🟢 적합*\n" + "\n".join(_line(j, v) for j, v in shown)
        if len(fit) > len(shown):
            text += f"\n  … 외 {len(fit) - len(shown)}건"
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": _clip(text)}})
    else:
        blocks.append({"type": "section",
                       "text": {"type": "mrkdwn", "text": "이번 주 '적합' 공고는 없습니다."}})

    if risk:
        blocks.append({"type": "context", "elements": [{
            "type": "mrkdwn",
            "text": f"🔴 위험 {len(risk)}건은 제목을 싣지 않습니다 — 노션에서 확인하세요."}]})

    if db_url:
        blocks.append({"type": "actions", "elements": [{
            "type": "button",
            "text": {"type": "plain_text", "text": "노션에서 전체 보기 →", "emoji": True},
            "url": db_url,
        }]})
    return blocks


def digest_text(week: str, results: list[tuple[JobPosting, Verdict]],
                *, top_n: int | None = None) -> str:
    """--dry-run 콘솔용 요약(슬랙과 같은 규칙: 위험은 건수만)."""
    top_n = top_n or settings.SLACK_TOP_N
    fit = [(j, v) for j, v in results if v.verdict == "적합"]
    mid = [(j, v) for j, v in results if v.verdict == "보통"]
    risk = [(j, v) for j, v in results if v.verdict == "위험"]
    fit.sort(key=lambda t: (roles.sort_key(t[0].role), -t[1].score))
    lines = [f"📋 {week} 구직 스카우트", "",
             f"신규 {len(results)}건 · 🟢 적합 {len(fit)} · ⚪ 보통 {len(mid)} · 🔴 위험 {len(risk)}",
             ""]
    if fit:
        lines.append("🟢 적합")
        for j, v in fit[:top_n]:
            lines.append(f"• [{j.company}] {j.title} — {' / '.join(v.positive_signals) or '-'}")
        if len(fit) > top_n:
            lines.append(f"  … 외 {len(fit) - top_n}건")
    else:
        lines.append("이번 주 '적합' 공고는 없습니다.")
    if risk:
        lines += ["", f"🔴 위험 {len(risk)}건 (제목 생략 — 노션에서 확인)"]
    return "\n".join(lines)
