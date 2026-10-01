"""직군 모집단 감사 — **검색이 돌려준 것이 그 직군이 맞는지 재는 도구.**

왜 필요한가 (2026-10-01):
  `--probe worknet --role public_it`이 키워드 `전산직`으로 **호원대 학사교직지원팀**을
  물어왔다. 업무가 "대학혁신지원사업 프로그램 / 재학생 유지관리"이고 공고 전체에서 "전산"이
  든 문자열은 `- 전산활용 가능자 우대` 하나뿐이다. 검색어가 우대사항 한 줄에 걸린 것이다.

  이게 왜 치명적인가 — "백엔드 공고의 38%가 Docker를 요구한다"를 내려면 **무엇이 백엔드
  공고인가**가 먼저 정의돼야 한다. 모집단에 행정직이 섞이면 그 38%는 편향이 없는 게 아니라
  **그냥 틀린 숫자**다. CLAUDE.md의 "통계는 전량에서 뽑는다"는 *적합도로 미리 거르지 말라*는
  뜻이고, 모집단이 올바르게 정의돼 있음을 전제한다.

  실패 방향이 갭 분석과 **반대**라는 점이 중요하다. 갭은 과소평가하지 않는 쪽이 안전하지만,
  모집단은 **느슨한 쪽이 위험하다** — 엄격해서 놓치면 표본 수가 줄어 눈에 보이고, 느슨해서
  섞이면 모든 비율이 조용히 희석된다.

왜 '판정'이 아니라 '감사'인가:
  판정 규칙을 지금 쓰면 **음성 예시 2건(둘 다 같은 대학)에 맞춘 것**이 된다. 양성 예시가
  0건이라 문턱을 정할 근거가 없다. 그래서 이 모듈은 거르지 않는다 — **신호를 나란히 찍어
  사람이 보게** 한다. 규칙은 이 출력이 쌓인 다음에 만든다.

읽는 법:
  `요건` 0건이 곧 오염은 아니다. 상세 수집 실패나 **사전 공백**일 수도 있다. 그래서 추출된
  스킬 이름을 같이 찍는다 — 0건인데 본문에 기술이 보이면 그건 오염이 아니라 사전 문제다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from config import roles
from src import requirements


@dataclass
class Row:
    """공고 1건의 직군 소속 신호. **판정하지 않는다 — 재기만 한다.**"""
    source_id: str
    title: str
    company: str
    keyword: str = ""              # 어느 검색어가 물어왔나. 오염원 추적의 핵심
    req_count: int = 0
    skills: tuple[str, ...] = ()
    computer_skill: str = ""       # 고용24 고정 선택지. 워드/엑셀만이면 사무직 신호
    worker_count: int | None = None
    detail_ok: bool = True         # 상세 수집 실패와 '요건이 정말 없음'을 구분한다

    @property
    def office_only(self) -> bool:
        """`컴퓨터 활용 능력`이 사무 도구뿐인가.

        호원대 행정직이 `문서작성 (워드프로세스 활용),표계산 (스프레드시트 활용)`이었다.
        **개발 공고가 이 칸에 뭘 적는지는 아직 모른다** — 그래서 판정이 아니라 표시다.
        """
        if not self.computer_skill:
            return False
        office = ("워드", "스프레드시트", "표계산", "문서작성", "프레젠테이션", "한글")
        return all(any(o in part for o in office)
                   for part in self.computer_skill.split(",") if part.strip())


@dataclass
class Audit:
    role: str
    rows: list[Row] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.rows)

    @property
    def no_requirements(self) -> int:
        return sum(1 for r in self.rows if r.detail_ok and r.req_count == 0)

    @property
    def detail_failed(self) -> int:
        return sum(1 for r in self.rows if not r.detail_ok)

    def by_keyword(self) -> dict[str, tuple[int, int]]:
        """검색어 -> (공고 수, 요건 0건 수). **어느 검색어가 오염원인지 바로 보인다.**"""
        out: dict[str, tuple[int, int]] = {}
        for r in self.rows:
            n, zero = out.get(r.keyword, (0, 0))
            out[r.keyword] = (n + 1, zero + (1 if r.detail_ok and r.req_count == 0 else 0))
        return out


def row_for(posting, detail, keyword: str = "") -> Row:
    """공고 + 상세 → 감사 1행. `detail`이 None이면 상세 수집 실패로 기록한다."""
    if detail is None:
        return Row(source_id=getattr(posting, "source_id", ""),
                   title=getattr(posting, "title", ""),
                   company=getattr(posting, "company", ""),
                   keyword=keyword, detail_ok=False)
    reqs = requirements.from_detail(detail)
    return Row(
        source_id=getattr(posting, "source_id", ""),
        title=getattr(posting, "title", ""),
        company=getattr(posting, "company", ""),
        keyword=keyword,
        req_count=len(reqs),
        skills=tuple(r.skill for r in reqs),
        computer_skill=getattr(detail, "computer_skill", "") or "",
        worker_count=getattr(detail, "worker_count", None),
    )


def report(audit: Audit) -> str:
    """사람이 읽을 감사표. **결론을 내리지 않는다** — 숫자와 제목을 나란히 둘 뿐이다."""
    out: list[str] = []
    name = roles.display_name(audit.role) if audit.role in roles.ROLES else audit.role
    out.append(f"직군 모집단 감사 — {name} ({audit.role})")
    out.append("=" * 72)

    if not audit.rows:
        out.append("공고 0건 — 검색어나 등록일 창을 확인할 것")
        return "\n".join(out)

    out.append(f"{'요건':>4} {'사무':>4} {'인원':>6}  제목")
    out.append("-" * 72)
    for r in sorted(audit.rows, key=lambda x: (x.detail_ok, x.req_count)):
        if not r.detail_ok:
            out.append(f"{'  —':>4} {'':>4} {'':>6}  {r.title[:46]}  (상세 실패)")
            continue
        office = "○" if r.office_only else ""
        cnt = "" if r.worker_count is None else str(r.worker_count)
        out.append(f"{r.req_count:>4} {office:>4} {cnt:>6}  {r.title[:46]}")
        if r.skills:
            out.append(f"{'':>17}  └ {', '.join(r.skills[:10])}")

    out.append("-" * 72)
    out.append(f"공고 {audit.total}건 · 요건 0건 {audit.no_requirements}건 "
               f"· 상세 실패 {audit.detail_failed}건")

    out.append("")
    out.append("검색어별:")
    for kw, (n, zero) in sorted(audit.by_keyword().items(), key=lambda x: -x[1][1]):
        flag = "  ← 오염 의심" if n and zero / n >= 0.5 else ""
        out.append(f"  {kw:<16} {n:>3}건 중 요건 0건 {zero:>3}건{flag}")

    out.append("")
    out.append("⚠️ 요건 0건이 곧 오염은 아니다. 스킬 사전에 없는 단어만 쓴 공고일 수도 있다 —")
    out.append("   제목이 개발 직군인데 0건이면 그건 모집단 문제가 아니라 사전 문제다.")
    return "\n".join(out)
