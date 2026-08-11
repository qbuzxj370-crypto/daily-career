# career-scout 작업 참조 (ref.md)

> **다른 세션이 이 프로젝트를 이어받을 때 가장 먼저 읽는 문서.**
> `docs/career-plan.md` = 무엇을 왜 만드는가(설계 정본), `CLAUDE.md` = 손댈 때 지켜야 할 규칙,
> **이 문서 = 지금 어디까지 됐고, 무엇이 검증됐고, 무엇이 아직 추측인가(진행 기록).**
>
> 기준일: **2026-08-10**. 코드를 바꿔 여기 적힌 사실이 달라지면 **이 문서도 같이 고칠 것.**

---

## 1. 한 줄 요약

P2~P5(수집·판정·발행·알림)는 구현·테스트 완료. 워크넷은 검색 필터(학력·경력·등록일)까지
실측으로 확정됐고, 제목 프리스크린과 3주 보관 정리가 추가됐다.

Gemini 프리스크린은 2026-08-11에 실호출로 검증됐다(138건 → 32건 통과, 인프라 공고 오탈락 0건).

**남은 것은 두 가지다.** ①P1 사람인 필드 확정 — API 키가 있어야 하므로 사용자가 `--probe`를
돌려야 한다(지금 사람인 매핑은 *문서 기준 추정치*). ②`--publish` 실제 발행 — 노션 페이지
생성(`pages.create`)은 아직 성공을 확인하지 못했다.

---

## 2. 현재 상태

| 단계 | 상태 | 검증 방법 |
|---|---|---|
| P1 워크넷 필드 확정 | ✅ **완료 (2026-08-10)** | 실제 페이지 `--probe` 통과, 실응답 픽스처로 테스트 |
| P1 사람인 필드 확정 | ❌ **미완 (승인 대기)** | `--probe saramin` 미실행 |
| P2 수집·정규화·규칙 신호 | ✅ 완료 | `--mock` 6건 전 경로 통과 |
| P3 LLM 배치 판정 | ✅ 완료 | 주입 테스트(배치→개별→규칙 폴백) 통과. **실제 Gemini 호출은 미실행** |
| P4 노션 발행·중복 제거 | ✅ 완료 | 가짜 클라이언트로 payload 검증 + **실제 DB 생성·스키마 실측 일치 확인**. 페이지 발행 자체는 미실행 |
| P5 슬랙·스케줄 | ✅ 완료 | `weekly.yml` 작성. **workflow_dispatch 수동 실행은 미검증** |
| 워크넷 검색 필터(학력·경력·등록일) | ✅ **완료 (2026-08-10)** | 실호출로 totalRecordCount 변화 확인 (614→20) |
| 제목 프리스크린 | ✅ **완료 (2026-08-11)** | 실제 Gemini 선별 실행 — 138건 → 32건 통과, 오탈락 0건 확인 |
| 보관 기간 정리(3주) | ✅ 완료 | 주입 테스트 + 실제 DB에 `--purge` 실행(0건, 무해 확인) |

테스트: `python -m pytest tests -q` → **95 passed** (키 0개, 네트워크 0회).
`test_rules 31 / test_sources 22 / test_state 14 / test_pipeline 12 / test_prescreen 9 / test_notion 7`.

### 실제로 돌려서 확인한 것

```
python -m pytest tests -q            → 95 passed
python -m src.pipeline --mock        → 수집 6 → 프리스크린 4 통과 → 적합 1 · 보통 3, 마크다운 + 슬랙 요약
python -m src.pipeline --dry-run --no-llm (키 없음) → 소스 비활성화 후 안내 메시지, exit 1
run_probe (HTTP 스텁 주입)            → probe/ 덤프 + 필드 추출표 + 빈 필드 경고 동작
python -m src.pipeline --init-db --parent-page 3b84cc12…  → DB 생성 성공 (2026-08-10)
python -m src.pipeline --probe worknet --role public_it   → 실제 페이지 파싱 성공, 전 필드 추출
python -m src.pipeline --dry-run --no-llm                 → 워크넷 150건 수집, 적합 2 · 보통 147 · 위험 1
# 필터 도입 후 (2026-08-10):
python -m src.pipeline --dry-run --no-llm  → 144건 수집 → 프리스크린 140건 통과 · 적합 2 · 보통 138
python -m src.pipeline --purge             → [purge] 2026-W31 이전 0건 보관 (DB가 비어 있어 무해)
```

**노션 DB는 실제로 만들어졌다 (2026-08-10).** 부모 페이지 `일주`(`3b84cc12-d76a-8031-87a0-e0330b4df131`,
워크스페이스 최상위) 아래에 `커리어 스카우트 (Career Scout)` 생성:

```
NOTION_DB_ID   = d189c28d-67a8-4479-b885-d9499d437fd7
data source id = 95ed4ea3-fd6d-4fc4-936f-99405802bbe9
```

생성 직후 실측 스키마를 `notion_pub._schema_properties()`와 대조해 **15개 속성 전부 타입 일치,
여분 속성 0개**를 확인했다. select 옵션도 정본과 일치한다(`Verdict` 3, `Role` 5, `Source` 2,
`Status` 5, `Kind` 2, `Signals` 4). 즉 `test_notion.py`가 가짜 클라이언트로 지키던 "쓰는 속성은 전부
스키마에 존재한다" 불변식이 **실제 DB에서도 성립**한다. `resolve_data_source_id()`도 실호출로 동작 확인.

`--mock` 결과 검증 포인트: `구축`+`주 5일` 공고가 가점 2개로 **적합**, `관제`+`구축` 겸업 공고는
**완화**되어 강제되지 않음.

⚠️ **프리스크린 도입 후 `--mock` 출력에서 위험 건이 사라졌다** — `24*365`/`4조 3교대` 공고와
`1인 전산` 공고는 규칙이 위험을 확정하므로 프리스크린 단계에서 먼저 빠진다(로그의
"규칙 확정 위험 2건"이 그 2건이다). 판정이 약해진 게 아니라 더 앞에서 걸린 것이다.
"위험 강제가 살아 있는가"는 이제 `--no-prescreen`이나 `tests/test_rules.py`로 확인한다.

### 아직 한 번도 실제로 부르지 않은 것

**사람인 API**와 **Slack Webhook**. 둘 다 주입/픽스처로만 검증됐다.

**Gemini 프리스크린은 2026-08-11에 검증 완료.** 138건 → 통과 32 / 제외 106(규칙 1 + 제목 105).
버려진 목록을 눈으로 훑어 **인프라·전산 공고가 잘못 버려진 건 0건**임을 확인했다. 특히 키워드
`라우터`가 끌어온 **CNC 라우터(목공 조각기) 공고 6건 이상을 정확히 걸러냈다** — 제목만으로도
동음이의어를 구분한다는 증거다. 개발 전용 공고(백엔드·PHP·AI Native)도 프롬프트 지시대로 제외됐다.

**본판정(evaluator)의 실제 Gemini 출력은 3건짜리 픽스처로만 확인했다**(모델 검증 과정에서
`--mock` 공고 3건을 실호출 → 적합/위험/보통이 기대대로 나옴). 전체 배치는 아직 안 돌렸다.
노션은 DB 생성·스키마 조회·`--purge`까지 실호출했고 **페이지 발행(`pages.create`)은 미검증**.
워크넷은 실제 수집까지 돌았다(필터 적용 후 138~144건).

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
  prescreen.py        제목+회사명만으로 1차 선별. 규칙 확정 위험은 LLM 없이 제외, 실패는 통과(fail-open)
  state.py            State/EmptyState/NotionState, week_exists, known_source_keys, purge_before, iso_week
  pipeline.py         CLI, _mock_jobs/MockSource, _publish_flow(주입), screen, run_probe, run_purge
tests/                95개. rules + 오케스트레이션 불변식 중심
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

13. **워크넷 검색에 학력·경력·등록일 필터를 건다** (2026-08-10 사용자 요청, `settings.py`).
    파라미터 이름과 코드값은 §7(1)의 실측표 참조. 기본값이 `학력무관+대졸(2~3년)`,
    `신입+관계없음`인 이유는 워크넷 공고 대부분이 '학력무관·경력무관'으로 등록돼 있어서
    `04`/`N`만 남기면 지원 가능한 공고의 대다수가 사라지기 때문이다(614 → 72). 엄격하게
    좁히려면 `WORKNET_ACADEMIC_GBN=04`, `WORKNET_CAREER_TYPES=N`으로 덮어쓴다.

14. **결과 0건 판별을 `totalRecordCount`로 바꿨다** (`sources/worknet.py`).
    이전 코드는 본문에 `chkboxWantedAuthNo` 문자열이 있으면 '레이아웃 변경'으로 봤는데,
    **0건 페이지에도 그 id를 참조하는 JS가 그대로 실려 온다.** 필터를 걸기 전에는 어떤
    키워드든 결과가 있어서 이 오판이 드러나지 않다가, 필터 도입 직후 `MSP`·`퍼블릭클라우드`
    등에서 가짜 `SourceError`가 쏟아지며 발견됐다. 지금은 총건수가 0이면 정상 0건,
    총건수가 있는데 행이 없으면 레이아웃 변경, 총건수 자체가 없으면 검색 페이지가 아님.

15. **제목 프리스크린 단계 신설** (`src/prescreen.py`, 2026-08-10 사용자 요청).
    "제미나이에 너무 많은 데이터를 주지 말자. 제목만 보고 추려서 맞지 않는 건 버리자."
    수집 → 신호 → **프리스크린** → 본판정 → 발행. 여기서 버려진 공고는 **노션에 아예 생기지
    않는다.** 두 가지 설계 원칙이 테스트로 고정돼 있다(`tests/test_prescreen.py`):
    - 규칙이 확정한 위험(`sig.forced`)은 LLM에 묻지 않고 코드가 먼저 버린다.
    - **실패는 통과 쪽으로 연다(fail-open).** 호출이 깨졌을 때 전부 버리면 그 주의 구직
      기회가 조용히 사라진다. 배치 실패 시 그 배치는 통째로 본판정으로 넘어간다.
    프롬프트에는 회사명과 제목만 들어간다 — `raw_text`가 새어 들어가면 이 단계의 의미가 없다.

16. **보관 기간 정리(`--purge`)는 create-only 원칙의 예외가 아니다.**
    "발행은 create-only"는 *판정 결과로 기존 페이지를 덮어쓰지 않는다*는 뜻이고, 정리는
    사용자가 명시적으로 요청한 별도 동작이다. 단 **`Status`가 `신규`가 아닌 페이지는 절대
    건드리지 않는다** — 지원까지 한 공고를 나이 때문에 지우면 사용자의 분류 작업이 사라진다.
    나이는 정리의 필요조건이지 충분조건이 아니다. 주차 비교는 `YYYY-Www` 문자열 사전순으로
    한다(0 패딩이라 시간순과 일치, `test_week_strings_compare_chronologically`가 지킨다).

17. **429/RESOURCE_EXHAUSTED는 개별 재시도를 건너뛴다** (`evaluator._judge_group`).
    일일 할당량이 끝난 상태에서 배치를 10건으로 쪼개 재시도하면 확실히 실패할 호출을 10번
    더 쏘면서 남은 배치의 quota까지 태운다. 파싱 실패처럼 쪼개면 풀리는 오류와 달리 이건
    쪼개도 안 풀린다 → 이 모델은 접고 바로 폴백 모델로 넘어간다.

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
python -m src.pipeline --probe saramin --role cloud     # ← 남은 것은 이것뿐
python -m src.pipeline --probe worknet --role public_it # (2026-08-10 완료)
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

#### 2026-08-10 워크넷 1차 시도 — 키 거부로 실패 (매핑 미확정)

`--probe worknet --role public_it` → HTTP 200이지만 본문이 오류 봉투:

```xml
<wantedRoot><message>유효하지 않은 인증키 입니다.</message><messageCd>002</messageCd></wantedRoot>
```

원인을 하나씩 배제했으니 **같은 것을 다시 의심하지 말 것**:

| 가설 | 결과 |
|---|---|
| 파라미터명이 `serviceKey`여야 함 | ❌ `serviceKey`로 보내면 `001 인증키값이 없습니다` — `authKey`가 맞다 |
| httpx가 키를 이중 인코딩 | ❌ URL에 직접 박아도 동일한 002 (키에 `%+/=` 없음, 36자, 하이픈 포함) |
| 엔드포인트가 work24.go.kr로 이관됨 | ❌ `openapi.work24.go.kr`는 연결 거부, `www.work24.go.kr/opi/...`는 404.
`openapi.work.go.kr`가 여전히 유효한 호스트다 |

원인은 **엔드포인트가 이관된 것**이었다. 공식 문서(고용24 채용정보 API 상세)의 현행 주소는

```
https://www.work24.go.kr/cm/openApi/call/wk/callOpenApiSvcInfo210L01.do
```

이고 구 주소 `openapi.work.go.kr/opi/opi/opia/wantedApi.do`는 고용24 발급 키를 002로 거부한다.
`config/settings.py`의 `WORKNET_API_URL` 기본값을 신규 주소로 교체했다(2026-08-10).

#### ⛔ 그런데 개인회원은 이 API를 쓸 수 없다 — 워크넷 소스 자체가 막혔다

신규 주소로 다시 호출하니 인증은 통과했고, 대신 이 응답이 왔다:

```
개인회원은 사용할 수 없는 OPEN-API입니다.
```

**키·엔드포인트·파라미터 문제가 아니라 계정 등급 제한이다.** 코드로 우회할 수 없고,
사용자는 구직자(개인회원)이므로 기업/기관 회원 전환도 현실적이지 않다.

배제 근거(같은 것을 다시 의심하지 말 것):

| 가설 | 결과 |
|---|---|
| 파라미터명이 `serviceKey`여야 함 | ❌ `serviceKey`로 보내면 `001 인증키값이 없습니다` — `authKey`가 맞다 |
| httpx가 키를 이중 인코딩 | ❌ URL에 직접 박아도 동일 (키에 `%+/=` 없음, 36자, 하이픈 포함) |
| 키가 미승인/무효 | ❌ 신규 주소에서는 인증을 통과했다 (오류가 등급 제한으로 바뀜) |
| `openapi.work24.go.kr` 호스트 | ❌ 연결 거부. `www.work24.go.kr/cm/openApi/...`가 정답 |

공공데이터포털 경유도 막다른 길이다. [워크넷 채용정보 데이터셋](https://www.data.go.kr/data/3038225/openapi.do)
은 **활용신청을 누르면 고용24로 넘긴다** — data.go.kr가 대행하지 않고 원 기관에 위임하는 형태라
같은 등급 제한에 다시 걸린다(사용자가 직접 확인). 근거는 세 갈래가 일치한다:
①실제 호출 거부 ②소개 페이지 "기업회원 전용" 명시 ③data.go.kr 위임.
**`DATA_GO_KR_KEY`는 폐기했다** — settings/weekly.yml/.env/career-plan에서 모두 제거됨.

#### ✅ 해결: 공개 검색 페이지 파싱으로 전환 (사용자 결정, 2026-08-10)

"공식 API만" 제약은 **워크넷에 한해** 뒤집혔다. robots.txt가 이 구분의 근거다:

| 사이트 | robots.txt | 결론 |
|---|---|---|
| work24.go.kr | `Allow: /` (차단: `/cm/common/`, `/sa/`, `/ei/`, `selectUnifySearchPost.do`) | 대상 경로 허용, 사이트맵에도 등재 |
| saramin.co.kr | `Disallow: /zf_user/recruit/` | **사람인은 절대 스크래핑 금지 — API만** |

확정된 요청 (브라우저 DevTools cURL로 관측, 문서 아님):

```
GET https://www.work24.go.kr/wk/a/b/1200/retriveDtlEmpSrchList.do
    ?srcKeyword={키워드}&searchMode=Y&resultCnt={n}&currentPageNo={p}
    &sortField=DATE&sortOrderBy=DESC&siteClcd=all
```

**`searchMode=Y`가 없으면 키워드가 조용히 무시되고 최신순 전체 목록이 온다.** 제목을 읽기 전까지는
검색이 된 것처럼 보이기 때문에 가장 빠지기 쉬운 함정이다. 폼 필드 141개를 통째로 재현해도
이 값이 없으면 안 걸린다 — 이걸 몰라 오래 헤맸다.

파싱 앵커(`src/sources/worknet.py`):

| 필드 | 앵커 |
|---|---|
| 행 분할 | `<tr id="listN">` |
| 공고번호·회사·제목 | 비교 체크박스 `value="공고번호\|정보구분\|회사명\|공고제목"` |
| 상세 URL | `href="/wk/a/b/1500/empDetailAuthView.do?..."` |
| 급여·경력/학력·근무형태·지역 | `<li class="dollar\|member\|time\|site">` |
| 마감일 | `var date = 'YYYY-MM-DD'` (폴백: `마감일 : YYYY-MM-DD`) |
| 총건수 | 페이징 스크립트의 `totalRecordCount : N` (폴백: `name="totalRecordCount"` hidden) |

`<li class="time">`에 `주5일`, `08:30 ~ 19:30`이 실려 오므로 **"주간 중심" 가점이 워크넷에서는
안정적으로 걸린다**(사람인은 이 정보가 없어 거의 안 걸린다). `raw_text`에 반드시 포함시킬 것.

`parse()`는 결과 0건과 레이아웃 변경을 구분한다 — 판별 기준은 **총건수**다(§4-14 참조).
총건수 0 = 정상 0건, 총건수는 있는데 행이 없음 = 레이아웃 변경(`SourceError`),
총건수 자체가 없음 = 검색 결과 페이지가 아님(`SourceError`). 조용히 0건을 반환하면
매주 빈 결과가 나와도 알 수 없기 때문에 뒤의 둘은 반드시 시끄럽게 실패해야 한다.

`tests/test_sources.py`의 워크넷 픽스처는 **실제 응답 행 2개**다(추정치 아님).

#### 검색 필터 — 실측표 (2026-08-10)

검색 페이지의 체크박스/버튼 `value`에서 코드값을 뽑고, `srcKeyword=클라우드` 기준
`totalRecordCount` 변화로 **실제로 걸리는지**까지 확인했다. 문서가 아니라 관측값이다.

| 조건 | 파라미터 | 값 | 실측 결과 |
|---|---|---|---|
| (없음) | — | — | 614건 |
| 경력 신입 | `careerTypes` | `N` | 52건 |
| 경력 신입+관계없음 | `careerTypes` | `N,Z` | **139건 (기본값)** |
| 학력 대졸(2~3년) | `academicGbn` | `04` | 72건 |
| 학력무관+대졸(2~3년) | `academicGbn` | `00,04` | **476건 (기본값)** |
| 등록일 최근 1주 | `regDateStdt`·`regDateEndt` | `20260804`·`20260810` | 125건 |
| 위 3개 전부 | | | **20건** |

학력 코드: `00`=학력무관 `01,02`=중졸이하 `03`=고졸 `04`=대졸(2~3년) `05`=대졸(4년) `06`=석사 `07`=박사.
경력 코드: `N`=신입 `E`=경력 `Z`=관계없음.

**함정 3개 (전부 실측으로 확인했고, 페이지 소스만 봐서는 재도출 안 된다):**

1. 체크박스의 `name`은 `careerType`(단수)인데 **서버가 읽는 건 `careerTypes`(복수)** 다.
   단수로 보내면 조용히 무시된다 — JS가 체크된 값을 모아 히든 필드에 담아 보내기 때문.
2. 등록일은 **`YYYYMMDD`** 여야 한다. 하이픈(`2026-08-04`)을 넣으면 목록이 **0건**이 된다.
3. `termSearchGbn=W-1`("1주 이내" 버튼)은 **화면 상태값일 뿐 서버가 무시한다.** 날짜 두 개를
   직접 계산해서 줘야 실제로 걸린다. 버튼의 JS 계산식은 `오늘-7+1` = 오늘-6일이라,
   금요일 실행 시 지난 토요일~오늘이 되어 주 단위로 빈틈 없이 이어진다.

### (2) 수집 텍스트 품질 확인 (P1 덤프를 본 직후)

사람인 검색 API는 **공고 본문을 주지 않는다**(메타데이터만). 그러면 `3교대`·`관제` 같은 문구
신호가 제목·업종·키워드 안에 드러난 경우에만 걸린다. P1 덤프에서 `raw_text`에 실제로 어떤
텍스트가 들어오는지 보고 `config/rules.py` 패턴을 조정할지 판단한다.
**해결책으로 사람인에 HTML 스크래핑을 붙이지 말 것** — robots.txt가 금지한다(§(1) 표 참조).
워크넷 예외는 robots.txt가 허용했기 때문에 성립한 것이지, "API가 없으면 긁는다"는 규칙이 아니다.

### (3) ★최우선 — Gemini 실호출 검증 (할당량이 회복된 다음 날 아침에)

**할당량은 모델당 하루 20요청**이라(§8) 하루에 한 번밖에 제대로 못 본다. 순서를 지킬 것 —
아래 첫 명령이 요청 3개, 두 번째가 나머지를 쓴다. 순서를 바꾸면 그날 검증이 날아간다.

```bash
# 1) 프리스크린만 (요청 3개) — 무엇을 버리는지 눈으로 본다. 이게 아직 한 번도 검증 안 됐다.
PYTHONPATH=. python -c "
from src import collector, prescreen, signals
jobs = collector.sort_for_output(collector.collect(None, None, log=lambda *a: None))
sigs = signals.analyze_all(jobs); kept, dropped = prescreen.prescreen(jobs, sigs)
print(prescreen.summary(kept, dropped))
for d in dropped: print('  -', d.company, '|', d.title, '|', d.reason)"

# 2) 그다음에 발행 (--init-db는 2026-08-10 완료. 다시 돌리면 DB가 하나 더 생긴다 — 실행 금지)
python -m src.pipeline --publish            # 1회차
python -m src.pipeline --publish            # 2회차 → 주차 멱등 스킵(0건)
```

**확인할 것:**
- 버려진 목록에 진짜 무관한 공고만 있는가(영업·마케팅·제조 등). 인프라 공고가 섞여 버려졌다면
  `prescreen.SYSTEM_PROMPT`가 너무 공격적인 것이다 — "애매하면 남긴다"를 강화할 것.
- 통과 건수가 40건을 넘으면 본판정이 요청 4개 이상을 쓴다. 20요청 한도를 넘기는지 계산할 것.
- 발행 후: `Signals`에 정본 이름만 있는지, `SourceKey`가 속성인지, 본문에 마크다운 표가 없는지,
  위험 건 Score가 2 이하인지.

`CollectedWeek`를 손으로 지난 주로 바꾼 뒤 재실행해 **같은 SourceKey가 다시 생성되지 않는지**
(주차 멱등과 별개인 중복 제거 레이어) 확인한다.

3주 보관 정리는 데이터가 3주 이상 쌓여야 실제로 지우는 동작을 볼 수 있다. 그전까지는
`--purge`가 0건을 보고하는 게 정상이다. 처음 실제로 지워지는 주에는 **Status를 바꿔 둔 페이지가
살아남는지** 반드시 눈으로 확인할 것(그게 이 기능의 유일한 위험 지점이다).

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
- 디렉터리가 `week-career` → **`daily-career`** 로 바뀌었고, 리포지토리는
  `https://github.com/kmj20021/daily-career.git` (daily-recall과 **분리된 별도 리포**).
- 노션 통합은 daily-recall과 같은 **`한루한문`** 을 재사용한다(토큰 공유, DB만 분리).
  부모 페이지를 통합에 **연결(Connections)** 하지 않으면 토큰이 유효해도 `ObjectNotFound`가 난다 —
  "토큰 무효"(`Unauthorized`)와 오류 코드가 다르므로 둘을 구분해서 읽을 것.
- **GitHub Actions Secrets는 로컬 실행에서 읽히지 않는다.** 러너 안에서만 주입되므로,
  로컬(`--probe`/`--dry-run`/`--init-db`)은 `.env`, CI는 Secrets — **같은 값을 양쪽에 각각** 넣어야 한다.
  `.env`는 `.gitignore` 1번 줄에 있고 git에 추적되지 않는 것을 확인했다.
- 진단 요령: 키가 안 먹으면 값을 보지 말고 **길이**부터 재라. 정상 길이는 노션 50, Gemini 53,
  고용24 36, `NOTION_DB_ID` 36(하이픈 포함). 접두사만 옮겨 적어 7자가 들어가 있던 사고가 있었다.

### ⚠️ 모델 이름은 `models.list()`를 믿지 말 것 (2026-08-11)

`gemini-2.5-flash-lite`는 **목록에는 뜨는데 호출하면 404**를 낸다:
`This model models/gemini-2.5-flash-lite is no longer available to new users.`
폴백 모델이 이 상태였고, 기본 모델이 429로 막힌 순간 폴백도 같이 죽어 프리스크린이
통째로 fail-open으로 넘어갔다(= 아무것도 걸러지지 않았다). 조용히 무력화된 셈이라
로그를 읽기 전까지는 "제미나이가 선별을 안 한다"로만 보였다.

**모델을 바꿀 땐 반드시 이 프로젝트의 `response_schema`로 실호출해 확인할 것.**
`gemini-3.5-flash-lite`는 호출은 되지만 우리 config를 `400 INVALID_ARGUMENT`로 거부한다
(목록에 있고 살아 있어도 못 쓰는 경우). 현재 값은 양쪽 스키마로 실호출 검증됐다:

```
MODEL          = gemini-3.5-flash        (구 gemini-2.5-flash)
MODEL_FALLBACK = gemini-3.1-flash-lite   (구 gemini-2.5-flash-lite — 404로 폐기)
```

폴백 모델은 "기본 모델이 오류일 때의 예비"이자 **"기본 모델이 하루 할당량을 다 썼을 때의
예비 할당량"** 이기도 하다(할당량은 모델별로 따로 센다). 그래서 둘은 반드시 달라야 한다.

### Gemini 무료 티어 한도 — 이 프로젝트의 실질적 상한 (2026-08-10 실측)

429 응답 본문에서 그대로 읽은 값이다(추정 아님):

```
quotaId    GenerateRequestsPerDayPerProjectPerModel-FreeTier
limit      20   model: gemini-2.5-flash
limit      20   model: gemini-2.5-flash-lite
```

**모델당 하루 20요청.** 분당(RPM)이 아니라 **일일(RPD)** 이 먼저 걸린다 —
이건 기다린다고 풀리지 않는다. 두 모델 합쳐 하루 40요청이 전부다.

**리셋은 미국 태평양시(PT) 자정 = KST 오후 4~5시다.** 한국 날짜가 바뀌었다고 초기화되지
않는다 — 2026-08-11 오전 9시(KST)에 다시 시도했을 때 여전히 전날 할당량이었고, 그래서
"다음날인데 왜 또 429냐"로 한 번 더 헤맸다. **실호출 검증은 KST 오후 5시 이후에 할 것.**

한 번 실행이 쓰는 요청 수(공고 144건 기준):

| 구성 | 요청 수 | 비고 |
|---|---|---|
| 프리스크린 없이 본판정만 | 15 (`144/EVAL_BATCH_SIZE`) | 하루치의 75%를 한 번에 소진 |
| 프리스크린(60건 묶음) | 3 | |
| 프리스크린 통과분 본판정 | 통과 건수/10 | |

그래서 프리스크린은 "제미나이에 부담을 덜 준다"는 취향 문제가 아니라 **한도 안에 들어오기
위한 필수 장치**다. 2026-08-10 발행 시도가 15배치를 돌려 그날 할당량을 다 썼고, 그 뒤 실행은
전부 429가 났다(그래서 프리스크린의 실제 선별 품질은 아직 검증되지 않았다 — §2 표 참조).

⚠️ 배치가 실패하면 `_judge_group`이 건별로 쪼개 재시도한다. 429일 때는 이 재시도를 건너뛰도록
막아 뒀지만(§4-17), 다른 이유로 실패하면 배치 1개가 요청 10개를 쓴다 — 한도가 20이라 배치
2개만 실패해도 그날이 끝난다. 실패 로그가 잦아지면 `EVAL_BATCH_SIZE`부터 의심할 것.

---

## 9. 세션 시작 체크리스트

1. 이 문서(`docs/ref.md`) → `CLAUDE.md` → 필요하면 `docs/career-plan.md` 순으로 읽는다.
2. `python -m pytest tests -q`로 95개가 초록인지 먼저 확인한다(회귀 여부 판단 기준선).
3. 소스 클라이언트를 건드릴 참이면 **P1이 끝났는지부터** 확인한다
   (`src/sources/*.py` 상단의 "P1 미검증" 경고 주석이 남아 있으면 아직 안 끝난 것).
4. 판정 기준(`config/rules.py`)을 바꾸면 `tests/test_rules.py`도 같이 바꾼다.
5. 작업이 끝나면 이 문서의 §2 상태표와 §4 결정 목록을 갱신한다.
