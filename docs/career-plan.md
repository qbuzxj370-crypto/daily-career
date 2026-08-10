# 커리어 스카우트 (career-scout) — 구현 계획

## Context

매주 금요일 아침, 사람인·워크넷 공식 API에서 **인프라/운영 트랙 공고**를 키워드로 수집하고,
LLM으로 **적합도를 판정**해 노션 DB에 쌓고 슬랙으로 요약을 push하는 개인 구직 도구.

해결하려는 문제는 "공고를 못 찾는 것"이 아니라 **"주당 수십 건을 눈으로 훑어 거르는 시간"** 이다.
이 트랙은 직무명이 비슷해도 실제 업무가 극단적으로 갈린다 — 같은 "시스템 엔지니어" 공고가
24/365 관제 상주일 수도, 구축팀과 같이 앉는 인프라 엔지니어일 수도 있다. 그래서 이 도구의
1차 목적은 **수집이 아니라 판정**이다.

기술 스택과 파이프라인 구조는 옆 디렉터리의 `daily-recall`을 그대로 따른다
(GitHub Actions cron + 노션 정본 + 슬랙 보조알림 + Gemini JSON 모드). 검증된 구조를 재사용해
새로 만드는 부분을 수집·판정 로직에만 집중시킨다.

---

## 판정 기준 (이 프로젝트의 존재 이유)

사용자가 지정한 기준. **구현 편의로 흐리면 도구가 목적을 잃는다.**

| 신호 | 방향 | 의미 |
|---|---|---|
| 관제 전담 | 🔴 위험 | 24/365 모니터링 상주, 교대근무. 커리어 정체 위험 |
| 소기업 / 오너 1인 | 🔴 위험 | 전산 담당 1인, 사수 없음, 대표 직속 |
| 구축 조직 동거 | 🟢 가점 | 구축팀·SI 조직과 같은 공간. 운영만 하지 않고 구축 경험 획득 가능 |
| 주간 중심 | 🟢 가점 | 상시 주간, 교대 없음 |

최종 표기는 **3단계**: `적합` / `보통` / `위험`.

---

## 검색 직무 (키워드 정본)

`config/roles.py`가 slug → (표시명, 검색 키워드, 가중치)를 보유. **slug가 정본 키.**

| slug | 표시명 | 검색 키워드 | 우선 |
|---|---|---|---|
| `cloud` | 클라우드 엔지니어 | 클라우드, AWS, MSP, 클라우드엔지니어, 퍼블릭클라우드 | ⭐ |
| `public_it` | 공공기관 전산직 | 전산직, 전산실, 정보화, 공공 전산, 정보시스템 운영 | ⭐ |
| `sys_ops` | 시스템 운영/SE | 시스템운영, 시스템엔지니어, 리눅스, 유닉스, 서버운영 | |
| `tech_support` | 기술지원 (TS/TA) | 기술지원, 테크니컬서포트, TA, 유지보수, 솔루션엔지니어 | |
| `network` | 네트워크 운영/지원 | 네트워크운영, 네트워크엔지니어, 스위치, 라우터, NE | |

- ⭐ = 주요 관심 직무. 슬랙 요약과 노션 정렬에서 먼저 노출된다.
- `전산실 운영(공공·병원·대학)`은 `public_it`에 포함하되, 키워드에 `병원 전산`, `대학 전산`을 추가한다.

---

## 파이프라인 흐름

```
GitHub Actions cron (매주 금요일 07:00 KST)
        ↓
① 멱등성: 이번 주(ISO week) 이미 수집했나? → 예: 종료
        ↓
② collector : 사람인 API + 워크넷 API를 직무 키워드별로 호출 → JobPosting 정규화
        ↓
③ dedup     : 노션에서 기존 SourceKey 전량 조회 → 신규 공고만 남김
        ↓
④ signals   : 규칙(정규식)으로 위험/가점 신호 추출  ← 코드가 확정하는 부분
        ↓
⑤ evaluator : Gemini JSON 모드로 verdict·점수·한줄요약 생성 (신호를 힌트로 주입)
        ↓
⑥ notion_pub: 공고 1건 = 페이지 1개 생성
        ↓
⑦ slack_pub : 주간 요약 push (적합 상위 N건 + 건수 통계 + 노션 링크)
```

`_publish_flow`는 `daily-recall/src/pipeline.py:41`과 동일하게 **의존성 주입** 형태로 만들어
실제 API 없이 오케스트레이션을 검증할 수 있게 한다.

---

## 아키텍처 결정

1. **공식 API만 사용. 스크래핑 없음.** 사람인 오픈 API와 공공데이터포털(고용24) 모두 무료 키
   발급으로 JSON을 준다. 약관 위반·차단·마크업 변경 리스크가 전부 사라진다.
2. **상태 정본 = 노션 DB.** 중복 제거·주차 멱등성이 전부 노션 쿼리에서 파생. 별도 상태 파일 없음
   (러너가 ephemeral이어도 무방). `daily-recall/src/state.py`의 `NotionState` 구조를 그대로 따른다.
3. **중복 제거 키 = `SourceKey = "{source}:{공고ID}"`.** 두 API 모두 공고 고유 ID를 준다.
   매 실행 시 노션에서 SourceKey 전량을 **한 번에** 조회해 메모리에서 필터링한다
   (공고당 1쿼리는 금물 — 수십 건이면 수십 왕복).
4. **규칙이 먼저, LLM이 나중.** 명확한 신호(`3교대`, `24*365 관제`)는 정규식으로 코드가 확정하고,
   그 결과를 LLM 프롬프트에 힌트로 주입한다. LLM이 다른 판단을 해도 **규칙이 확정한 위험 신호는
   코드가 강제로 되돌린다** — `daily-recall/src/generator.py:146`이 category/difficulty를 컨텍스트
   값으로 덮어쓰는 것과 같은 패턴.
5. **LLM 호출은 배치.** 공고 40건을 개별 호출하면 무료 티어 RPM 제한에 걸린다. 10건씩 묶어
   배열로 판정받는다(주당 약 4~5회 호출). 배치 실패 시 해당 배치만 개별 호출로 재시도.
6. **슬랙은 보조 알림.** 전송 실패해도 노션(정본)은 무사하므로 예외를 삼키고 진행.

---

## 파일 레이아웃

```
career-scout/
  config/
    settings.py      # env 로드, 튜닝 상수
    roles.py         # 직무 slug → (표시명, 키워드, 우선순위)  ★정본
    rules.py         # 위험/가점 신호 정규식  ★정본
  src/
    sources/
      base.py        # JobPosting 데이터클래스 + Source 프로토콜
      saramin.py     # 사람인 오픈 API 클라이언트
      worknet.py     # 공공데이터포털(고용24) 클라이언트
    collector.py     # 여러 소스 실행 + 정규화 + 소스 내 중복 제거
    signals.py       # 규칙 기반 신호 추출
    evaluator.py     # Gemini 배치 판정 (daily-recall generator.py 대응)
    renderer.py      # → 마크다운 / 노션 블록 / 슬랙 블록
    md_to_notion.py  # daily-recall/src/md_to_notion.py 그대로 복사 (수정 불필요)
    notion_pub.py    # 페이지 생성 + init_db + error 페이지
    slack_pub.py     # Incoming Webhook
    state.py         # NotionState (SourceKey 집합, 주차 멱등성)
    pipeline.py      # CLI 엔트리
  .github/workflows/weekly.yml
  .env.example
  requirements.txt   # google-genai, notion-client, httpx, python-dotenv
```

### 재사용 (새로 짜지 말 것)

| 가져올 것 | 출처 | 수정량 |
|---|---|---|
| `md_to_notion.py` 전체 | `daily-recall/src/md_to_notion.py` | 없음 — 마크다운 부분집합 계약이 동일 |
| `NotionState._query()` 페이지네이션 | `daily-recall/src/state.py:75` | 없음 |
| `resolve_data_source_id()` | `daily-recall/src/state.py:51` | 없음 (Notion 2025-09 data source 처리) |
| `_is_missing_select_option()` | `daily-recall/src/state.py:38` | 없음 (신규 select 옵션 400 흡수) |
| `init_db()` / `_schema_properties()` 패턴 | `daily-recall/src/notion_pub.py:53` | 속성 스키마만 교체 |
| `slack_pub._post()` | `daily-recall/src/slack_pub.py:14` | 없음 |
| `_publish_flow` 주입 구조 | `daily-recall/src/pipeline.py:41` | 단계만 교체 |

---

## 데이터 계약

### JobPosting (내부 정규화 형식)

```python
@dataclass
class JobPosting:
    source: str          # "saramin" | "worknet"
    source_id: str       # 공고 고유 ID
    title: str
    company: str
    url: str
    role: str            # 매칭된 직무 slug
    location: str | None
    experience: str | None   # "신입", "경력 1~3년" 등
    employment_type: str | None
    deadline: str | None     # YYYY-MM-DD
    raw_text: str        # 판정에 넣을 텍스트(제목+업종+근무형태+키워드 등 합본)

    @property
    def source_key(self) -> str:
        return f"{self.source}:{self.source_id}"
```

### Verdict (LLM 반환 → 내부 표준)

```json
{
  "source_key": "saramin:12345678",
  "verdict": "적합",
  "score": 4,
  "summary": "AWS 기반 MSP 운영, 구축팀과 동일 조직에서 이관 업무 병행",
  "positive_signals": ["구축 조직 동거", "주간 중심"],
  "risk_signals": [],
  "reason": "구축·운영을 한 팀에서 담당하고 상시 주간 근무 명시"
}
```

- `verdict`는 `적합` / `보통` / `위험` enum. `score`는 1~5 정수.
- 규칙이 확정한 위험 신호가 있으면 **코드가 `verdict`를 `위험`으로 덮어쓴다.**

### 노션 DB 스키마

| 속성 | 타입 | 용도 |
|---|---|---|
| Title | title | `{회사} · {공고제목}` |
| Company | rich_text | |
| Verdict | select | 적합 / 보통 / 위험 |
| Score | number | 1~5 |
| Role | select | 직무 표시명 (5개 옵션) |
| Signals | multi_select | 구축 조직 동거 / 주간 중심 / 관제 전담 / 소기업·1인 |
| Source | select | 사람인 / 워크넷 |
| SourceKey | rich_text | **중복 제거 키** — 반드시 속성에 저장(본문은 쿼리 불가) |
| URL | url | 공고 원문 |
| Deadline | date | 마감일 |
| Location | rich_text | |
| Experience | rich_text | |
| CollectedWeek | rich_text | ISO 주차 `2026-W33` — 주차 멱등성 판정 |
| Status | select | 신규 / 검토중 / 지원 / 보류 / 탈락 ← **사람이 손으로 관리** |
| Kind | select | job / error |

본문에는 한줄 요약·판정 근거·신호를 마크다운으로 렌더한다.

---

## 신호 규칙 (`config/rules.py`)

정규식 → 신호명. 대소문자·공백 무시.

```python
RISK = {
    "관제 전담": [r"관제", r"\bNOC\b", r"24[\s*×x/]?365", r"3교대", r"4조\s?3교대",
                 r"교대\s?근무", r"모니터링\s?요원", r"상주\s?관제"],
    "소기업·1인": [r"1인\s?전산", r"전산\s?담당\s?1명", r"대표\s?직속", r"사원수\s?[1-9]명"],
}
POSITIVE = {
    "구축 조직 동거": [r"구축", r"\bSI\b", r"설계.{0,10}구축", r"인프라\s?구축",
                    r"이관", r"마이그레이션", r"\bPoC\b"],
    "주간 중심": [r"주\s?5일", r"09:?00.{0,10}18:?00", r"상시\s?주간",
                r"교대\s?없", r"주간\s?근무"],
}
```

**완화 규칙**: `관제`가 잡혔더라도 `구축 조직 동거` 신호가 함께 있으면 코드가 `위험`을 강제하지
않고 LLM 판단에 맡긴다(운영+구축 겸업 공고를 통째로 버리지 않기 위함). `3교대`·`24*365` 같은
근무형태 신호는 완화 없이 무조건 `위험`.

---

## 튜닝 상수 (`config/settings.py`)

- `MODEL` / `MODEL_FALLBACK` — `gemini-2.5-flash` / `gemini-2.5-flash-lite` (env 오버라이드)
- `EVAL_BATCH_SIZE = 10` — LLM 배치당 공고 수
- `MAX_JOBS_PER_ROLE = 30` — 직무당 수집 상한 (프롬프트·비용 팽창 방지)
- `SLACK_TOP_N = 5` — 슬랙에 제목까지 싣는 적합 공고 수
- `ROLE_PRIORITY` — `cloud`, `public_it` 우선 노출
- `SEND_SLACK = bool(SLACK_WEBHOOK_URL)`

## 시크릿

| 이름 | 발급처 |
|---|---|
| `SARAMIN_ACCESS_KEY` | 사람인 오픈API 신청 (무료) |
| `DATA_GO_KR_KEY` | 공공데이터포털 고용24 채용정보 API 활용신청 (무료) |
| `GEMINI_API_KEY` | Google AI Studio — **daily-recall 것 재사용 가능** |
| `NOTION_API_KEY` | Notion 내부 통합 — **daily-recall 통합 재사용 가능** |
| `NOTION_DB_ID` | `--init-db`로 새로 생성 (daily-recall과 별도 DB) |
| `SLACK_WEBHOOK_URL` | 선택 — 별도 채널 권장 (`#job-scout`) |

---

## CLI

```
python -m src.pipeline --probe saramin --role cloud   # 원본 응답 1건 덤프 (P1 필드 확정용)
python -m src.pipeline --mock                         # API 미호출 픽스처로 전 경로 검증
python -m src.pipeline --dry-run                      # 실제 수집·판정, 노션 미발행(stdout)
python -m src.pipeline --dry-run --no-llm             # 수집+규칙 신호만 (LLM 비용 0)
python -m src.pipeline --publish                      # 실제 발행 (주차 멱등)
python -m src.pipeline --init-db --parent-page <ID>   # 최초 1회 노션 DB 생성
```

---

## 단계별 구현

### P1 — 소스 확정 (가장 먼저, 다른 모든 단계의 전제)
API 키 2개를 발급하고 `--probe`로 **실제 응답을 파일로 덤프**해 필드 매핑을 확정한다.
문서만 보고 매핑을 짜지 말 것 — 실제 응답과 어긋나는 경우가 흔하다.

확정할 것:
- 사람인: 공고 ID·제목·회사명·마감일·경력·지역 필드의 실제 경로, 페이지네이션 파라미터,
  **회사 규모(사원수) 제공 여부**
- 워크넷: 엔드포인트 URL, 인증 파라미터명, 응답 포맷(JSON/XML), 공고번호 필드

> ⚠️ **알려진 한계**: 사람인 검색 API가 사원수를 주지 않을 가능성이 높다. 그러면 "소기업·오너 1인"
> 판정은 공고 문구에서만 추론해야 하고 정확도가 떨어진다. P1에서 확인 후, 미제공이면
> LLM이 `"판단 불가"`를 반환하도록 허용하고 노션에서 사람이 최종 확인하는 흐름으로 간다.
> **이 한계를 숨기고 있는 척하지 않는다.**

### P2 — 수집 + 정규화 + 규칙 신호
`sources/`, `collector.py`, `signals.py`, `--mock` / `--dry-run --no-llm`.
산출물: 직무 5개 × 소스 2개를 돌려 `JobPosting` 리스트가 나오고, 각 건에 신호가 붙는다.

### P3 — LLM 판정
`evaluator.py`. Gemini JSON 모드(`response_schema`)로 배열 반환. 배치 실패 시 개별 재시도 →
모델 폴백. 규칙 확정 신호로 verdict 강제 덮어쓰기.

### P4 — 노션 발행 + 중복 제거
`state.py`, `notion_pub.py`, `--init-db`. SourceKey 전량 조회 → 신규만 발행.
실패 시 `Kind=error` 페이지 남기고 예외 재전파.

### P5 — 슬랙 + 스케줄
`slack_pub.py`, `.github/workflows/weekly.yml`.
cron: **`0 22 * * 4`** (UTC 목 22:00 = **KST 금 07:00**).

---

## 슬랙 메시지 형태

```
📋 2026-W33 구직 스카우트

신규 23건 · 🟢 적합 4 · ⚪ 보통 12 · 🔴 위험 7

🟢 적합
• [메가존클라우드] AWS 클라우드 운영 엔지니어 (신입) — 구축팀 동거 / 주간
• [한국전자통신연구원] 정보시스템 운영 — 공공, 상시주간
  … 외 2건

[ 노션에서 전체 보기 → ]
```

위험 공고는 **건수만** 표시하고 제목을 싣지 않는다 — 슬랙을 훑는 시간까지 아끼는 것이 목적.

---

## 검증

**P1 (소스)**
```
python -m src.pipeline --probe saramin --role cloud
python -m src.pipeline --probe worknet --role public_it
```
→ 각 소스에서 공고 ID·제목·회사명·URL·마감일이 빠짐없이 추출되면 통과.

**P2 (수집·신호)**
```
python -m src.pipeline --mock
python -m src.pipeline --dry-run --no-llm
```
→ 픽스처에 `3교대` 문구를 넣은 공고가 `관제 전담` 위험 신호를 받고, `구축`+`주 5일` 공고가
가점 신호 2개를 받으면 통과. (규칙 정규식은 픽스처로 직접 검증 — 이 부분만은 테스트를 둘 것)

**P3 (판정)**
```
python -m src.pipeline --dry-run
```
→ 전 공고가 Verdict 스키마를 통과하고, 규칙이 `위험` 확정한 건이 LLM 출력과 무관하게
`위험`으로 나오면 통과.

**P4 (발행·중복)**
```
python -m src.pipeline --publish     # 1회차: N건 생성
python -m src.pipeline --publish     # 2회차: 주차 멱등 스킵, 0건 생성
```
→ 노션에서 페이지 수가 늘지 않으면 통과. `CollectedWeek`를 지난 주로 손수 바꾼 뒤 재실행해
**같은 SourceKey가 다시 생성되지 않는지**(주차 멱등과 별개인 SourceKey 중복 제거) 확인.

**P5 (스케줄)**
GitHub Actions에서 `workflow_dispatch`로 수동 1회 실행 → 노션 생성 + 슬랙 수신 확인.
슬랙 메시지에 위험 공고 제목이 없는지 확인.

---

## 미결 / 나중에

- 사원수·기업규모 보강: 사람인 API가 미제공이면 공공데이터포털의 기업정보 API로 회사명 조인을
  검토 (P1 결과 확인 후 판단)
- `Status`를 사람이 `지원`으로 바꾼 공고의 마감일 D-3 리마인더
- 같은 회사가 매주 재공고하는 케이스 감지 (상시 채용 = 이직률 신호일 수 있음)
