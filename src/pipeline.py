"""오케스트레이션 CLI.

  python -m src.pipeline --probe saramin --role cloud   # 원본 응답 덤프 (P1 필드 확정용)
  python -m src.pipeline --mock                         # API 미호출 픽스처로 전 경로 검증
  python -m src.pipeline --dry-run                      # 실제 수집·판정, 노션 미발행(stdout)
  python -m src.pipeline --dry-run --no-llm             # 수집+규칙 신호만 (LLM 비용 0)
  python -m src.pipeline --publish                      # 실제 발행 (주차 멱등)
  python -m src.pipeline --purge                        # 오래된 주차 페이지만 정리
  python -m src.pipeline --init-db --parent-page <ID>   # 최초 1회 노션 DB 생성
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

from config import roles, settings
from src import collector, evaluator, prescreen, renderer, signals
from src.evaluator import Verdict
from src.sources.base import JobPosting, SourceError
from src.state import NotionState, State, iso_week, purge_cutoff

PROBE_DIR = Path(__file__).resolve().parent.parent / "probe"


# ---------------------------------------------------------------- 픽스처(--mock)

def _mock_jobs() -> list[JobPosting]:
    """오프라인 검증용 픽스처. docs/career-plan.md §검증 P2가 요구하는 케이스를 담는다.

    - `3교대` 문구 → 관제 전담 위험 신호(완화 불가, verdict 강제 위험)
    - `구축` + `주 5일` → 가점 2개
    - `관제` + `구축` → 위험 후보가 완화되어 강제되지 않음
    - `1인 전산` → 소기업·1인 강제 위험
    """
    def j(sid, role, company, title, text, **kw) -> JobPosting:
        return JobPosting(source="mock", source_id=sid, title=title, company=company,
                          url=f"https://example.com/jobs/{sid}", role=role,
                          raw_text=f"{title} | {company} | {text}", **kw)

    return [
        j("1001", "cloud", "메가존클라우드", "AWS 클라우드 운영 엔지니어 (신입)",
          "클라우드 인프라 구축 및 운영, 온프렘 이관 프로젝트 참여, 주 5일 상시 주간 근무",
          location="서울 강남구", experience="신입", employment_type="정규직",
          deadline="2026-09-30"),
        j("1002", "sys_ops", "OO정보기술", "IDC 시스템 관제 엔지니어",
          "24*365 관제센터 상주, 4조 3교대 근무, 서버 모니터링 및 장애 1차 대응",
          location="경기 성남시", experience="신입·경력", employment_type="정규직"),
        j("1003", "public_it", "OO대학교", "전산실 정보시스템 운영 담당",
          "교내 정보시스템 운영 및 유지보수, 상시 주간, 주 5일 09:00~18:00",
          location="대전", experience="신입", employment_type="계약직", deadline="2026-09-10"),
        j("1004", "sys_ops", "OO엔지니어링", "시스템 엔지니어 (운영+구축)",
          "고객사 인프라 구축 및 관제 업무 병행, 신규 도입 프로젝트 설계·구축 참여",
          location="서울 마포구", experience="경력 1~3년", employment_type="정규직"),
        j("1005", "tech_support", "OO상사", "전산 담당자 채용",
          "1인 전산 담당으로 사내 PC/서버 유지보수 전반, 대표 직속 보고",
          location="인천", experience="무관", employment_type="정규직"),
        j("1006", "network", "OO네트웍스", "네트워크 운영 엔지니어",
          "스위치·라우터 구성 관리 및 회선 장애 대응",
          location="서울 영등포구", experience="신입", employment_type="정규직"),
    ]


class MockSource:
    """--mock 전용 소스. 키가 없어도 collector→signals→render 전 경로가 돈다."""
    name = "mock"

    def __init__(self, jobs: list[JobPosting] | None = None):
        self.jobs = jobs if jobs is not None else _mock_jobs()

    def fetch(self, keyword: str, role: str, limit: int) -> list[JobPosting]:
        return [job for job in self.jobs if job.role == role][:limit]

    def probe(self, keyword: str) -> tuple[str, str]:
        raise NotImplementedError("mock 소스는 probe를 지원하지 않는다")


# ---------------------------------------------------------------- 공통 단계

def _pair(jobs: list[JobPosting], verdicts: dict[str, Verdict]) -> list[tuple[JobPosting, Verdict]]:
    return [(job, verdicts[job.source_key]) for job in jobs if job.source_key in verdicts]


def screen(jobs: list[JobPosting], *, use_llm: bool, enabled: bool | None = None,
           log=print) -> tuple[list[JobPosting], dict[str, signals.SignalResult]]:
    """신호 추출 + 제목 프리스크린. (남길 공고, 전체 신호) 반환.

    신호는 **버려진 건까지 포함해** 전부 계산한다 — 프리스크린이 규칙 확정 위험을
    먼저 걸러내려면 그 값이 먼저 있어야 한다.
    """
    sigs = signals.analyze_all(jobs)
    if not (settings.PRESCREEN if enabled is None else enabled):
        return jobs, sigs
    kept, dropped = prescreen.prescreen(jobs, sigs, use_llm=use_llm, log=log)
    log(prescreen.summary(kept, dropped))
    return kept, sigs


def collect_and_judge(sources, role_slugs, *, use_llm: bool,
                      use_prescreen: bool | None = None, log=print):
    """수집 → 신호 → 프리스크린 → 판정. (jobs, sigs, verdicts) 반환."""
    jobs = collector.sort_for_output(collector.collect(sources, role_slugs, log=log))
    log(f"수집 {len(jobs)}건 (중복 제거 후)")
    jobs, sigs = screen(jobs, use_llm=use_llm, enabled=use_prescreen, log=log)
    verdicts = evaluator.evaluate(jobs, sigs, use_llm=use_llm, log=log)
    return jobs, sigs, verdicts


# ---------------------------------------------------------------- 발행 경로

def _publish_flow(state: State, *, week: str, collect_fn, evaluate_fn,
                  publisher, error_publisher, slack_fn=None, screen_fn=None,
                  purge_weeks: int | None = None, db_url: str = "", log=print) -> str:
    """발행 오케스트레이션(주입 가능 — 실제 API 없이 검증 가능).

    0) 보관 기간 정리: purge_weeks가 주어지면 그보다 오래된 주차 페이지를 먼저 치운다.
       실패해도 발행을 막지 않는다(정리는 보조 작업이다).
    1) 주차 멱등: 이번 ISO 주차에 이미 발행됐으면 아무것도 하지 않는다.
    2) SourceKey 중복 제거: 이전 주차에 이미 본 공고는 다시 만들지 않는다(멱등 레이어 2).
    3) 프리스크린: screen_fn이 버린 공고는 **판정도 발행도 하지 않는다**(노션에 안 남는다).
    4) 수집/판정 자체가 실패하면 error 페이지를 남기고 예외 전파(스케줄러가 실패로 인지).
    5) 개별 페이지 생성 실패는 나머지 발행을 막지 않는다. 끝난 뒤 error 페이지 + 예외.
    6) 슬랙은 보조 알림 — 실패해도 발행 성공을 무효화하지 않는다.
    """
    if purge_weeks:
        try:
            state.purge_before(purge_cutoff(week, purge_weeks), log=log)
        except Exception as e:  # noqa: BLE001 — 정리 실패가 이번 주 발행을 막을 이유는 없다
            log(f"  [경고] 오래된 페이지 정리 실패(무시): {type(e).__name__}: {e}")

    if state.week_exists(week):
        return f"[skip] {week} 이미 수집됨 — 주차 멱등 스킵"

    try:
        jobs = collect_fn()
        known = state.known_source_keys()
        new_jobs = [j for j in jobs if j.source_key not in known]
        log(f"신규 {len(new_jobs)}건 (기존 SourceKey {len(known)}건과 대조)")
        if not new_jobs:
            return f"[skip] {week} 신규 공고 0건 — 발행 없음"
        if screen_fn is not None:
            new_jobs = screen_fn(new_jobs)
            if not new_jobs:
                return f"[skip] {week} 프리스크린 통과 0건 — 발행 없음"
        sigs, verdicts = evaluate_fn(new_jobs)
    except Exception as e:  # noqa: BLE001
        error_publisher(f"{type(e).__name__}: {e}", week)
        raise

    published: list[tuple[JobPosting, Verdict]] = []
    failed: list[str] = []
    for job in new_jobs:
        v = verdicts.get(job.source_key)
        if v is None:
            failed.append(f"{job.source_key}: 판정 누락")
            continue
        try:
            publisher(job, v, sigs.get(job.source_key, signals.SignalResult()), week)
            published.append((job, v))
        except Exception as e:  # noqa: BLE001 — 1건 실패가 나머지를 막지 않는다
            failed.append(f"{job.source_key}: {type(e).__name__}: {e}")
            log(f"  [경고] 페이지 생성 실패 {job.source_key}: {type(e).__name__}: {e}")

    if slack_fn is not None and published:
        try:
            slack_fn(week, published, db_url)
        except Exception as e:  # noqa: BLE001 — 슬랙은 보조 알림
            log(f"  [경고] Slack 단계 예외(무시): {type(e).__name__}: {e}")

    if failed:
        error_publisher(f"{len(failed)}건 발행 실패:\n" + "\n".join(failed[:10]), week)
        raise RuntimeError(f"{len(published)}건 발행 / {len(failed)}건 실패: {failed[0]}")
    return f"[published] {week} · {len(published)}건 발행"


# ---------------------------------------------------------------- 실행 모드

def run_offline(*, mock: bool, use_llm: bool, role_slugs: list[str] | None,
                week: str, use_prescreen: bool | None = None, log=print) -> str:
    sources = [MockSource()] if mock else None
    jobs, sigs, verdicts = collect_and_judge(sources, role_slugs, use_llm=use_llm,
                                             use_prescreen=use_prescreen, log=log)
    results = _pair(jobs, verdicts)

    out: list[str] = []
    for job, v in results:
        out.append(renderer.to_markdown(job, v, sigs[job.source_key], week))
        out.append("=" * 72)
    out.append(renderer.digest_text(week, results))
    return "\n".join(out)


def run_publish(*, use_llm: bool, role_slugs: list[str] | None, week: str,
                purge: bool = True, use_prescreen: bool | None = None,
                keep_weeks: int | None = None, log=print) -> str:
    from src import notion_pub
    slack_fn = None
    if settings.SEND_SLACK:
        from src import slack_pub
        slack_fn = slack_pub.send_digest

    def collect_fn():
        return collector.sort_for_output(collector.collect(None, role_slugs, log=log))

    def screen_fn(jobs):
        return screen(jobs, use_llm=use_llm, enabled=use_prescreen, log=log)[0]

    def evaluate_fn(jobs):
        sigs = signals.analyze_all(jobs)
        return sigs, evaluator.evaluate(jobs, sigs, use_llm=use_llm, log=log)

    return _publish_flow(
        NotionState(), week=week,
        collect_fn=collect_fn, evaluate_fn=evaluate_fn, screen_fn=screen_fn,
        purge_weeks=(keep_weeks or settings.PURGE_KEEP_WEEKS) if purge else None,
        publisher=notion_pub.publish, error_publisher=notion_pub.publish_error,
        slack_fn=slack_fn, db_url=notion_pub.database_url(), log=log,
    )


def run_purge(week: str, keep_weeks: int | None = None, log=print) -> str:
    """오래된 주차 페이지만 정리(발행 없이). --purge 전용."""
    cutoff = purge_cutoff(week, keep_weeks)
    purged, held = NotionState().purge_before(cutoff, log=log)
    return (f"[purge] {cutoff} 이전 {purged}건 보관"
            + (f" · Status가 '신규'가 아니라 남긴 것 {held}건" if held else "")
            + f" (최근 {keep_weeks or settings.PURGE_KEEP_WEEKS}개 주차 유지)")


def run_probe(source_name: str, role_slug: str, log=print) -> int:
    """P1: 원본 응답을 파일로 덤프하고 파싱 결과 1건을 보여준다.

    **문서만 보고 매핑을 짜지 말 것** — 이 덤프가 sources/*.py의 FIELDS를 확정한다.
    """
    if source_name == "saramin":
        from src.sources.saramin import SaraminSource as Cls
    elif source_name == "worknet":
        from src.sources.worknet import WorknetSource as Cls
    else:
        log(f"알 수 없는 소스: {source_name} (saramin | worknet)")
        return 2

    src = Cls()
    keyword = roles.keywords(role_slug)[0]
    log(f"probe: {source_name} / role={role_slug} / keyword={keyword!r}")
    try:
        ext, body = src.probe(keyword)
    except SourceError as e:
        log(f"probe 실패: {e}")
        return 1

    PROBE_DIR.mkdir(exist_ok=True)
    path = PROBE_DIR / f"{source_name}_{role_slug}.{ext}"
    path.write_text(body, encoding="utf-8")
    log(f"원본 응답 덤프 → {path}  ({len(body):,} bytes)")
    log("-" * 72)
    log(body[:1500])
    log("-" * 72)

    postings = Cls.parse(body, role_slug)
    log(f"파싱 결과: {len(postings)}건")
    for p in postings[:2]:
        for k, v in p.to_dict().items():
            log(f"  {k:<16}: {v}")
        log("")
    missing = [k for k, v in (postings[0].to_dict().items() if postings else []) if not v]
    if missing:
        log(f"⚠️ 비어 있는 필드: {missing} → sources/{source_name}.py FIELDS 후보 경로를 "
            f"위 원본 응답에 맞게 고칠 것")
    elif postings:
        log("✓ 공고ID·제목·회사명·URL·마감일이 모두 추출됨 — P1 통과 기준 충족")
    return 0


# ---------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="career-scout 주간 구직 스카우트")
    p.add_argument("--probe", metavar="SOURCE", default=None,
                   help="원본 응답 1건 덤프 (saramin | worknet) — P1 필드 확정용")
    p.add_argument("--mock", action="store_true", help="API 미호출, 픽스처로 전 경로 검증")
    p.add_argument("--dry-run", action="store_true", help="실제 수집·판정, 노션 미발행")
    p.add_argument("--no-llm", action="store_true", help="LLM 미호출, 규칙 신호만(비용 0)")
    p.add_argument("--publish", action="store_true", help="노션에 실제 발행(주차 멱등)")
    p.add_argument("--purge", action="store_true",
                   help=f"오래된 주차 페이지만 정리(최근 {settings.PURGE_KEEP_WEEKS}주 유지). "
                        "Status가 '신규'가 아닌 페이지는 건드리지 않는다")
    p.add_argument("--no-purge", action="store_true",
                   help="--publish 시작 시 자동 정리를 건너뛴다")
    p.add_argument("--no-prescreen", action="store_true",
                   help="제목 프리스크린 없이 수집된 전부를 본판정·발행한다")
    p.add_argument("--keep-weeks", type=int, default=None,
                   help=f"보관할 주차 수(기본 {settings.PURGE_KEEP_WEEKS}, 이번 주 포함)")
    p.add_argument("--init-db", action="store_true", help="Notion DB를 스키마대로 생성(최초 1회)")
    p.add_argument("--parent-page", default=None,
                   help="--init-db: DB를 만들 부모 페이지 id(통합에 공유 필요)")
    p.add_argument("--role", default=None, help="직무 slug 고정(미지정 시 전체)")
    p.add_argument("--week", default=None, help="ISO 주차 고정(기본: 이번 주 KST)")
    args = p.parse_args(argv)

    if args.role and args.role not in roles.SLUGS:
        print(f"알 수 없는 role slug: {args.role} (가능: {', '.join(roles.SLUGS)})",
              file=sys.stderr)
        return 2
    role_slugs = [args.role] if args.role else None
    week = args.week or iso_week()

    if args.init_db:
        if not args.parent_page:
            print("--init-db에는 --parent-page <PAGE_ID> 필요(통합에 공유된 페이지)",
                  file=sys.stderr)
            return 2
        from src import notion_pub
        try:
            db = notion_pub.init_db(args.parent_page)
        except Exception as e:  # noqa: BLE001
            print(f"DB 생성 실패: {type(e).__name__}: {e}\n"
                  "(부모 페이지가 통합에 공유됐는지, NOTION_API_KEY가 맞는지 확인)",
                  file=sys.stderr)
            return 1
        sources = db.get("data_sources", [])
        ds_id = sources[0]["id"] if sources else "(없음)"
        print(f"✓ DB 생성 완료\n  NOTION_DB_ID={db.get('id', '')}\n  data source id={ds_id}\n"
              "→ 위 NOTION_DB_ID를 .env / GitHub Secrets에 등록하세요.")
        return 0

    if args.probe:
        return run_probe(args.probe, args.role or "cloud")

    if args.purge:
        try:
            print(run_purge(week, args.keep_weeks))
        except Exception as e:  # noqa: BLE001
            print(f"정리 실패: {type(e).__name__}: {e}", file=sys.stderr)
            return 1
        return 0

    if not (args.dry_run or args.mock or args.publish):
        print("--probe | --mock | --dry-run | --publish | --purge | --init-db 중 하나 필요",
              file=sys.stderr)
        return 2

    use_prescreen = False if args.no_prescreen else None
    try:
        if args.publish:
            print(run_publish(use_llm=not args.no_llm, role_slugs=role_slugs, week=week,
                              purge=not args.no_purge, use_prescreen=use_prescreen,
                              keep_weeks=args.keep_weeks))
            return 0
        # --mock은 키가 없는 오프라인 경로이므로 LLM도 호출하지 않는다.
        use_llm = not (args.no_llm or args.mock)
        print(run_offline(mock=args.mock, use_llm=use_llm, role_slugs=role_slugs, week=week,
                          use_prescreen=use_prescreen))
        return 0
    except collector.CollectError as e:
        print(f"\n수집 실패 — 공고를 한 건도 가져오지 못했습니다.\n{e}", file=sys.stderr)
        # 안내를 원인별로 가른다. 연결 실패에 `--probe`(구조 점검)를 권하면
        # 멀쩡한 페이지를 뜯어보게 된다 — 2026-08-28에 실제로 그렇게 새어 나갔다.
        if getattr(e, "unreachable", False):
            print("→ 서버에 **닿지 못했습니다**(연결/타임아웃). 페이지 구조 문제가 아니므로 "
                  "`--probe`로 확인할 것이 없습니다. 실행 위치의 네트워크 경로를 보세요 — "
                  "같은 명령이 다른 망(예: 국내 회선)에서 되는지부터 확인하면 갈립니다.",
                  file=sys.stderr)
        else:
            print("→ 사람인은 .env의 SARAMIN_ACCESS_KEY를 확인하고, 워크넷은 키가 없으니 "
                  "`--probe worknet`으로 페이지 구조가 바뀌지 않았는지 확인하세요.",
                  file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
