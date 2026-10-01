# career-scout (주간 구직 스카우트)

매주 금요일 아침, 사람인·워크넷 공식 API에서 인프라/운영 트랙 공고를 모아 **적합/보통/위험**으로
판정해 노션에 쌓고 슬랙으로 요약을 보낸다. 목적은 수집이 아니라 **판정** — 주당 수십 건을
눈으로 훑어 거르는 시간을 없애는 것이다. 설계 근거는 `docs/career-plan.md`, 작업 규칙은 `CLAUDE.md`,
**진행 상황·검증 현황·구현 결정 기록은 `docs/ref.md`** (이어서 작업한다면 여기부터 읽을 것).

## 설치

**→ 처음이면 [`SETUP.md`](SETUP.md)를 따라간다.** 순서에 의미가 있다 — 성립 조건 두 개
(수집 가능한가 / 상세 본문이 오는가)가 **키 없이** 확인되므로, 키 발급보다 먼저 한다.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # 키 채우기
python -m pytest tests -q # 205 passed 가 기준선
```

## 명령

```bash
python -m src.pipeline --mock                         # 키 없이 전 경로 검증(오프라인)
python -m src.pipeline --probe saramin --role cloud   # P1: 원본 응답 덤프 → probe/
python -m src.pipeline --probe worknet --role public_it
python -m src.pipeline --dry-run --no-llm             # 실제 수집 + 규칙 신호만(LLM 비용 0)
python -m src.pipeline --dry-run                      # 실제 수집 + LLM 판정, 노션 미발행
python -m src.pipeline --init-db --parent-page <ID>   # 최초 1회 노션 DB 생성
python -m src.pipeline --publish                      # 실제 발행(주차 멱등, 시작 시 3주 정리)
python -m src.pipeline --purge                        # 오래된 주차 페이지만 정리
python -m pytest tests -q                             # 규칙 회귀 테스트
```

`--role <slug>`로 직무를 하나로 좁힐 수 있다(`cloud`, `public_it`, `sys_ops`, `tech_support`,
`network`). `--week 2026-W33`으로 주차를 고정할 수 있다.
`--no-prescreen`은 제목 선별을 끄고 수집한 전부를 판정·발행하며, `--no-purge`는 발행 시작 시의
정리를 건너뛴다. `--keep-weeks N`으로 보관 주차 수를 바꿀 수 있다.

## 현재 상태

| 단계 | 상태 |
|---|---|
| P2 수집·정규화·규칙 신호 | 구현 완료 (`--mock` 통과) |
| P3 LLM 배치 판정 | 구현 완료 (주입 테스트 통과, 실제 Gemini 호출은 키 필요) |
| P4 노션 발행·중복 제거 | 구현 완료 (주입 테스트 통과, 실제 발행은 키 필요) |
| P5 슬랙·스케줄 | 구현 완료 (`weekly.yml`, 수동 dispatch 검증 필요) |
| P1 워크넷 필드 확정 | **완료** — 실제 페이지로 검증, 학력·경력·등록일 필터까지 실측 확정 |
| **P1 사람인 필드 확정** | **미완 — 승인 후 `--probe saramin`을 돌려야 한다** |

## 어떻게 공고 수를 줄이는가

수집한 걸 전부 노션에 쌓으면 결국 노션에서 다시 훑어야 한다. 그래서 3단계로 줄인다.

1. **검색 필터** — 워크넷 검색 자체에 조건을 건다. 서버가 걸러 주므로 가장 싸다.

   | 조건 | 기본값 | env |
   |---|---|---|
   | 경력 | 신입 + 관계없음 | `WORKNET_CAREER_TYPES=N,Z` |
   | 학력 | 학력무관 + 대졸(2~3년) | `WORKNET_ACADEMIC_GBN=00,04` |
   | 등록일 | 최근 7일(금요일 실행 시 지난 토요일~오늘) | `WORKNET_REG_DAYS=7` |

   기본값에 '학력무관·경력무관'을 넣은 이유: 워크넷 공고 대부분이 그렇게 등록돼 있어서
   `대졸(2~3년)`만 남기면 지원 가능한 공고 대다수가 사라진다(실측 614건 → 72건).
   엄격하게 좁히려면 `WORKNET_ACADEMIC_GBN=04`, `WORKNET_CAREER_TYPES=N`으로 덮어쓰면 된다.

2. **제목 프리스크린** — 회사명과 제목만 제미나이에 보내 1차로 추린다. 여기서 버려진 공고는
   판정도 받지 않고 **노션에 아예 만들어지지 않는다.** 규칙이 확정한 위험(3교대·24/365 등)은
   LLM에 묻지도 않고 코드가 먼저 버린다. 반대로 **호출이 실패하면 전부 통과시킨다** — 429 한 번에
   그 주 구직 기회가 통째로 사라지는 것보다 판정을 몇 건 더 하는 쪽이 낫다.
   끄려면 `--no-prescreen` 또는 `CS_PRESCREEN=0`.

3. **3주 보관** — `--publish`는 시작할 때 3주보다 오래된 페이지를 보관 처리한다.
   단 **`Status`를 손댄 페이지(검토중/지원/보류/탈락)는 건드리지 않는다.** 나이만으로는
   지우지 않는다는 뜻이다. `--purge`로 따로 돌릴 수 있고, `--no-purge`로 끌 수 있다.

> ⚠️ **제미나이 무료 티어는 모델당 하루 20요청이다**(2026-08-10 실측). 분당이 아니라 일일 한도라
> 기다린다고 풀리지 않는다. 공고 144건을 그냥 판정하면 요청 15개(하루치의 75%)를 한 번에 쓴다.
> 프리스크린은 취향이 아니라 이 한도 안에 들어오기 위한 장치다.

## 소스: 사람인은 API, 워크넷은 스크래핑

고용24 OPEN-API는 **기업회원 전용**이라 개인 계정으로는 열리지 않는다(소개 페이지 명시 + 실제
호출 시 `개인회원은 사용할 수 없는 OPEN-API입니다`). 공공데이터포털도 이 데이터셋은 고용24로
위임한다. 그래서 워크넷만 **공개 검색 페이지 파싱**으로 전환했다.

robots.txt가 이 구분을 결정한다:

| 사이트 | robots.txt | 방식 |
|---|---|---|
| work24.go.kr | `Allow: /` (대상 경로는 사이트맵에도 등재) | HTML 파싱 |
| saramin.co.kr | `Disallow: /zf_user/recruit/` | **공식 API만** — 절대 스크래핑 금지 |

work24 통합검색(`/cm/f/c/0100/selectUnifySearchPost.do`)도 robots.txt 금지 경로라 쓰지 않는다.

사람인 승인 후:

```bash
python -m src.pipeline --probe saramin --role cloud
```

를 돌리면 원본 응답이 `probe/`에 저장되고, 추출 결과와 **비어 있는 필드 목록**이 출력된다.
비는 필드가 있으면 `src/sources/saramin.py`의 `FIELDS`를 실제 응답에 맞게 고친다.

### ⚠️ 회사 규모는 판정할 수 없다

두 소스 모두 사원수와 공고 본문을 주지 않는다. 따라서 "소기업·오너 1인" 위험은 공고 문구에
명시된 경우(`1인 전산`, `대표 직속` 등)에만 잡히고, 그 외에는 `company_size = 판단 불가`가 된다.
이 값은 노션 페이지 본문에 "공고에 정보 없음 — 직접 확인 필요"로 그대로 노출된다.
**추측으로 메우지 않는다.**

다만 워크넷 목록은 근무형태(`주5일`, `08:30 ~ 19:30`)를 실어주기 때문에 "주간 중심" 가점은
워크넷 공고에서 안정적으로 걸린다.

## 파이프라인

```
GitHub Actions cron (목 22:19 UTC = 금 07:19 KST)
  ① state.week_exists   이번 ISO 주차에 이미 수집? → 예: 종료
  ② collector           사람인·워크넷 × 직무 키워드 → JobPosting 정규화
  ③ state.known_source_keys  기존 SourceKey 전량 1회 조회 → 신규만 남김
  ④ signals             정규식으로 위험/가점 신호 확정      ← 코드가 판단
  ⑤ evaluator           Gemini JSON 배치 판정(신호를 힌트 주입) ← 규칙이 이긴다
  ⑥ notion_pub          공고 1건 = 페이지 1개 (create-only)
  ⑦ slack_pub           주간 요약(위험은 건수만)
```

## 손대기 전에 알아야 할 것

- **규칙이 LLM을 이긴다.** `config/rules.py`의 hard 패턴(`3교대`, `24*365`)이 잡히면
  LLM 출력과 무관하게 verdict가 `위험`으로 강제된다. `관제`만 잡히고 `구축 조직 동거`가
  함께 있으면 완화되어 LLM 판단에 맡긴다(운영+구축 겸업 공고를 버리지 않기 위함).
- **멱등 레이어가 둘이다.** `CollectedWeek`(같은 주 재실행 차단)와 `SourceKey`(지난 주에 본
  공고 재생성 차단). 하나를 지우면 다른 하나가 우회되는 순간 중복 페이지가 생긴다.
- **`Status`는 사람 열이다.** 파이프라인은 생성 시 `신규`만 넣고 기존 페이지를 절대 수정하지
  않는다. "갱신·동기화" 기능을 붙이면 사용자의 분류 작업이 지워진다.
- **위험 공고 제목은 슬랙에 싣지 않는다.** 탈락 공고를 나열하면 없애려던 스캔이 되살아난다.
- **마크다운 표 금지.** `md_to_notion.py`(daily-recall에서 그대로 복사)가 표를 파싱하지 못한다.
- 테스트는 `config/rules.py`(판정 기준)와 오케스트레이션 불변식만 검증한다 —
  이 둘이 조용히 깨지면 도구가 목적을 잃기 때문이다.
