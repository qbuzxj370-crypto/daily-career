# career-scout 작업 참조 (ref.md)

> **다른 세션이 이 프로젝트를 이어받을 때 가장 먼저 읽는 문서.**
> `docs/career-plan.md` = 무엇을 왜 만드는가(설계 정본), `CLAUDE.md` = 손댈 때 지켜야 할 규칙,
> **이 문서 = 지금 어디까지 됐고, 무엇이 검증됐고, 무엇이 아직 추측인가(진행 기록).**
>
> 기준일: **2026-08-10**. 코드를 바꿔 여기 적힌 사실이 달라지면 **이 문서도 같이 고칠 것.**

---

## 1. 한 줄 요약

P2~P5(수집·판정·발행·알림)는 구현·테스트 완료. **P1(실제 API 응답으로 필드 매핑 확정)만 남았고,
그건 API 키가 있어야 하므로 사용자가 `--probe`를 돌려야 한다.** 지금 소스 클라이언트의 필드
매핑은 *문서 기준 추정치*다.

---

## 2. 현재 상태

| 단계 | 상태 | 검증 방법 |
|---|---|---|
| P1 소스 필드 확정 | ❌ **미완 (키 필요)** | `--probe` 미실행 |
| P2 수집·정규화·규칙 신호 | ✅ 완료 | `--mock` 6건 전 경로 통과 |
| P3 LLM 배치 판정 | ✅ 완료 | 주입 테스트(배치→개별→규칙 폴백) 통과. **실제 Gemini 호출은 미실행** |
| P4 노션 발행·중복 제거 | ✅ 완료 | 가짜 클라이언트로 payload 검증. **실제 노션 발행은 미실행** |
| P5 슬랙·스케줄 | ✅ 완료 | `weekly.yml` 작성. **workflow_dispatch 수동 실행은 미검증** |

테스트: `python -m pytest tests -q` → **69 passed** (키 0개, 네트워크 0회).
`test_rules 31 / test_sources 15 / test_pipeline 11 / test_notion 7 / test_state 5`.

### 실제로 돌려서 확인한 것

```
python -m pytest tests -q            → 69 passed
python -m src.pipeline --mock        → 수집 6 → 적합 1 · 보통 3 · 위험 2, 마크다운 + 슬랙 요약 출력
python -m src.pipeline --dry-run --no-llm (키 없음) → 소스 비활성화 후 안내 메시지, exit 1
run_probe (HTTP 스텁 주입)            → probe/ 덤프 + 필드 추출표 + 빈 필드 경고 동작
```

`--mock` 결과 검증 포인트: `24*365`/`4조 3교대` 공고와 `1인 전산` 공고가 **위험 강제**,
`구축`+`주 5일` 공고가 가점 2개로 **적합**, `관제`+`구축` 겸업 공고는 **완화**되어 강제되지 않음.

### 아직 한 번도 실제로 부르지 않은 것

사람인 API, 워크넷 API, Gemini API, Notion API, Slack Webhook. 전부 **주입/픽스처로만** 검증됐다.

---

## 3. 파일 맵

```
config/
  settings.py    env 로드 + 튜닝 상수 (env 접두사 CS_)
  roles.py       ★정본: 5개 직무 slug → (표시명, 키워드), PRIORITY_SLUGS
  rules.py       ★정본: RISK(hard/soft) · POSITIVE 정규식, MITIGATED_BY, ALL_SIGNAL_NAMES
src/
  sources/base.py     JobPosting 데이터클래스, Source 프로토콜, http_get/norm_date/first_str
  sources/saramin.py  사람인 클라이언트. FIELDS = **P1 미검증 추정 매핑**
  sources/worknet.py  워크넷 클라이언트. XML·JSON 양쪽 파싱, 엔드포인트/인증파라미터 env 교체 가능
  collector.py        직무×키워드×소스 수집, 실행 내 중복 제거, 소스 비활성화, CollectError
  signals.py          analyze(text) → SignalResult(risk/positive/forced/mitigated/hits)
  evaluator.py        Verdict, RESPONSE_SCHEMA, 배치 판정, rule_verdict, apply_rules(규칙 강제)
  renderer.py         to_markdown / to_slack_blocks / digest_text
  md_to_notion.py     daily-recall에서 **바이트 동일 복사**(sha256 확인). 수정 금지
  notion_pub.py       publish(create-only) / publish_error / init_db / _schema_properties
  slack_pub.py        send_digest, _post (실패 삼킴)
  state.py            State/EmptyState/NotionState, week_exists, known_source_keys, iso_week
  pipeline.py         CLI, _mock_jobs/MockSource, _publish_flow(주입), run_probe
tests/                69개. rules + 오케스트레이션 불변식 중심
.github/workflows/weekly.yml   cron "0 22 * * 4" (UTC 목22시 = KST 금07시), 발행 전 pytest 실행
```

---

## 4. 계획서에 없던 구현 결정 (이유 포함)

새 세션이 "왜 이렇게 돼 있지?" 하고 되돌리기 쉬운 것들. **되돌리기 전에 이유를 먼저 볼 것.**

1. **정규식을 `hard` / `soft`로 등급화** (`config/rules.py`)
   계획서의 완화 규칙("`관제`가 잡혀도 `구축 조직 동거`가 있으면 강제하지 않는다, 단 `3교대`·
   `24*365`는 완화 없음")을 코드로 표현하려면 같은 신호명 안에서 등급을 나눠야 했다.
   `hard` = 무조건 강제, `soft` = `MITIGATED_BY`에 완화 신호가 있고 그게 함께 잡히면 강제 안 함.
   **패턴의 등급을 바꾸는 것 = 사용자의 판정 기준을 바꾸는 것.** 반드시 테스트를 함께 고칠 것.

2. **`소기업·1인`에는 완화 규칙이 없다.** `MITIGATED_BY`에 `관제 전담`만 있다. 계획서가 완화를
   언급한 대상이 관제뿐이기 때문. `1인 전산`(hard)도 `대표 직속`(soft)도 결국 위험을 강제한다.

3. **`Verdict.company_size`를 추가하되 노션 속성으로는 만들지 않았다.**
   사원수 미제공이라는 알려진 한계 때문에 LLM이 `판단 불가`를 반환할 통로가 필요했지만,
   노션 DB 스키마는 계획서 표 그대로 유지하는 편이 낫다고 판단했다. 값은 **페이지 본문**에
   "회사 규모: 판단 불가 ← 공고에 정보 없음. 노션에서 직접 확인 필요"로 노출된다.

4. **verdict가 `위험`이면 score를 2 이하로 누른다** (`evaluator.apply_rules`).
   규칙이 verdict만 뒤집고 점수를 그대로 두면 노션·슬랙 정렬에서 위험 공고가 위로 올라온다.

5. **개별 페이지 생성 실패는 나머지 발행을 막지 않는다** (`pipeline._publish_flow`).
   1건 실패로 전체를 중단하면 이미 만든 페이지와 안 만든 페이지가 섞인 채 주차 멱등이 걸려
   나머지가 영영 안 들어온다. 지금은 끝까지 발행 → 슬랙 → `Kind=error` 페이지 → 예외 전파.
   ⚠️ **남은 트레이드오프**: 실패한 건은 같은 주에 재실행해도 주차 멱등에 막혀 재시도되지 않는다.
   다음 주 실행에서 그 공고가 검색 결과에 남아 있으면 그때 들어온다.

6. **신규 공고 0건이면 아무것도 쓰지 않는다.** 따라서 `CollectedWeek`도 안 남고, 같은 주에
   다시 돌리면 수집을 또 한다(발행은 여전히 0건). 쓸 게 없으니 무해하다고 판단.

7. **키가 없는 소스는 첫 실패 후 비활성화** (`collector`). 안 그러면 "미설정" 경고가
   키워드×소스 수십 번 찍힌다. 모든 호출이 실패하면 `CollectError` → CLI가 안내 후 exit 1.

8. **`state._query()`에 `limit` 파라미터 추가.** daily-recall 원본에는 없다. `week_exists`가
   `page_size=1`로 전량 페이지네이션을 도는 것을 막으려고 조기 종료를 넣었다(1쿼리로 끝난다).

9. **`_signal_names()`가 LLM이 지어낸 신호명을 버린다** (`notion_pub`).
   multi_select 옵션은 `config/rules.py`의 이름만 정본이다. 안 그러면 "야근 많음" 같은 옵션이
   노션 DB에 누적된다.

10. **`roles.py`가 우선순위 정본**, `settings.ROLE_PRIORITY`는 재노출일 뿐이다.
    (CLAUDE.md가 "튜닝 상수는 settings"라고 해서 이름은 settings에 두되 값은 roles에서 가져온다.)

11. **`PER_KEYWORD_FETCH = 20`** 신설. 계획서에는 직무당 상한(`MAX_JOBS_PER_ROLE=30`)만 있어서
    "한 번 호출에 몇 건 요청할지"가 비어 있었다.

12. **env 접두사는 `CS_`** (`CS_MODEL`, `CS_MODEL_FALLBACK`, `CS_HTTP_TIMEOUT`).
    daily-recall의 `DR_`와 섞이지 않게. 두 프로젝트가 같은 `.env`를 쓰더라도 안전하다.

---

## 5. 데이터 계약 (구현 기준, 계획서와 동일)

- `JobPosting`: `source, source_id, title, company, url, role(slug), location, experience,
  employment_type, deadline(YYYY-MM-DD), raw_text, extra` + `source_key = "{source}:{source_id}"`
- `Verdict`: `source_key, verdict(적합|보통|위험), score(1~5), summary, positive_signals[],
  risk_signals[], reason, company_size(대기업|중견기업|중소기업|소기업·1인|판단 불가)`
  + 내부 플래그 `by_rule`, `llm_failed`
- 노션 속성 15개: `Title, Company, Verdict, Score, Role, Signals, Source, SourceKey, URL,
  Deadline, Location, Experience, CollectedWeek, Status, Kind`
  (`tests/test_notion.py::test_schema_has_every_property_publish_writes`가 스키마와 실제 쓰기의
  불일치를 잡는다 — 속성을 추가하면 `_schema_properties()`도 같이 고쳐야 한다.)

---

## 6. 절대 깨면 안 되는 것 (테스트가 지키고 있음)

| 불변식 | 지키는 테스트 |
|---|---|
| 규칙이 확정한 위험은 LLM `적합`을 이긴다 | `test_rules::test_rules_override_llm_verdict` |
| `관제`+`구축`은 강제하지 않는다 | `test_rules::test_control_mitigated_by_build_org` |
| `3교대`는 `구축`이 있어도 강제한다 | `test_rules::test_shift_not_mitigated_by_build_org` |
| 멱등 레이어 2개가 각각 동작 | `test_pipeline::test_week_idempotency_*`, `test_source_key_dedup_*` |
| 슬랙에 위험 공고 제목 없음 | `test_pipeline::test_slack_never_lists_risky_titles` |
| SourceKey는 본문이 아니라 속성 | `test_notion::test_source_key_is_a_property_not_body_text` |
| 발행은 create-only, Status=신규 | `test_notion::test_status_is_created_as_new_only` |
| 본문에 마크다운 표 없음 | `test_notion::test_body_has_no_markdown_table` |
| SourceKey 조회는 일괄 페이지네이션 | `test_state::test_known_source_keys_paginates_in_one_sweep` |

---

## 7. 다음에 할 일

### (1) P1 — 소스 필드 확정 ★최우선, 키 필요

```bash
python -m src.pipeline --probe saramin --role cloud
python -m src.pipeline --probe worknet --role public_it
```

원본 응답이 `probe/{source}_{role}.{json|xml}`에 저장되고, 추출된 `JobPosting`과
**비어 있는 필드 목록**이 출력된다. 절차:

1. 빈 필드가 있으면 → 덤프를 보고 `src/sources/{source}.py`의 `FIELDS` 후보 경로를 실제 경로로 교체.
2. 워크넷이 401/오류를 내면 → 엔드포인트가 data.go.kr 경유일 수 있다.
   `.env`에서 `WORKNET_API_URL`, `WORKNET_AUTH_PARAM=serviceKey`, `WORKNET_EXTRA_PARAMS`로 교정.
3. 매핑이 확정되면 → `tests/test_sources.py`의 픽스처를 **실제 덤프로 교체**하고,
   각 파일 상단의 "P1 미검증" 경고 주석을 지운다.
4. `probe/`는 `.gitignore` 대상(공고 원문 포함) — 커밋하지 말 것.

> ⚠️ `tests/test_sources.py`가 초록이라고 매핑이 맞는 게 아니다. 그 픽스처는 **내 추정치**이고,
> 파서가 "추정한 모양"을 다루는지만 증명한다.

### (2) 수집 텍스트 품질 확인 (P1 덤프를 본 직후)

사람인 검색 API는 **공고 본문을 주지 않는다**(메타데이터만). 그러면 `3교대`·`관제` 같은 문구
신호가 제목·업종·키워드 안에 드러난 경우에만 걸린다. P1 덤프에서 `raw_text`에 실제로 어떤
텍스트가 들어오는지 보고 `config/rules.py` 패턴을 조정할지 판단한다.
**해결책으로 HTML 스크래핑을 붙이지 말 것 — 공식 API만 쓴다는 것은 의도된 제약이다.**

### (3) P3/P4 실호출 검증

```bash
python -m src.pipeline --dry-run --no-llm --role cloud   # 수집만 (LLM 비용 0)
python -m src.pipeline --dry-run --role cloud            # + 실제 Gemini 판정
python -m src.pipeline --init-db --parent-page <PAGE_ID> # 노션 DB 생성 → NOTION_DB_ID 등록
python -m src.pipeline --publish                          # 1회차
python -m src.pipeline --publish                          # 2회차 → 주차 멱등 스킵(0건)
```

`CollectedWeek`를 손으로 지난 주로 바꾼 뒤 재실행해 **같은 SourceKey가 다시 생성되지 않는지**
(주차 멱등과 별개인 중복 제거 레이어) 확인한다.

### (4) P5 스케줄 검증

GitHub Actions에서 `workflow_dispatch` 수동 1회 → 노션 생성 + 슬랙 수신 확인,
**슬랙 메시지에 위험 공고 제목이 없는지** 확인.

### (5) 나중에 (docs/career-plan.md §미결)

사원수 보강(공공데이터포털 기업정보 API 조인), `Status=지원` 공고 마감 D-3 리마인더,
같은 회사 주간 재공고 감지.

---

## 8. 환경 메모

- 이 PC의 `google-genai`는 `pydantic_core` 버전 충돌로 import가 깨져 있다
  (`ImportError: cannot import name 'from_json' from 'pydantic_core'`).
  지연 import라 `--mock`/테스트에는 영향 없지만 `--dry-run`(실제 LLM) 전에
  `pip install -U pydantic google-genai` 필요. CI는 `requirements.txt`로 새로 설치하므로 무관.
- `notion-client`는 정상 설치돼 있다. `pytest` 8.3.3, Python 3.11.9 (CI는 3.12).
- `docs/career-plan.md`는 원래 리포지토리 루트에 있었고 2026-08-10에 `docs/`로 이동했다.
  코드·문서의 참조 경로는 전부 `docs/career-plan.md`로 갱신됨.

---

## 9. 세션 시작 체크리스트

1. 이 문서(`docs/ref.md`) → `CLAUDE.md` → 필요하면 `docs/career-plan.md` 순으로 읽는다.
2. `python -m pytest tests -q`로 69개가 초록인지 먼저 확인한다(회귀 여부 판단 기준선).
3. 소스 클라이언트를 건드릴 참이면 **P1이 끝났는지부터** 확인한다
   (`src/sources/*.py` 상단의 "P1 미검증" 경고 주석이 남아 있으면 아직 안 끝난 것).
4. 판정 기준(`config/rules.py`)을 바꾸면 `tests/test_rules.py`도 같이 바꾼다.
5. 작업이 끝나면 이 문서의 §2 상태표와 §4 결정 목록을 갱신한다.
