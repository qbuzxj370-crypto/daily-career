# career-scout (주간 구직 스카우트)

매주 금요일 아침, 사람인·워크넷 공식 API에서 인프라/운영 트랙 공고를 모아 **적합/보통/위험**으로
판정해 노션에 쌓고 슬랙으로 요약을 보낸다. 목적은 수집이 아니라 **판정** — 주당 수십 건을
눈으로 훑어 거르는 시간을 없애는 것이다. 설계 근거는 `docs/career-plan.md`, 작업 규칙은 `CLAUDE.md`,
**진행 상황·검증 현황·구현 결정 기록은 `docs/ref.md`** (이어서 작업한다면 여기부터 읽을 것).

## 설치

```bash
pip install -r requirements.txt
cp .env.example .env      # 키 채우기
```

## 명령

```bash
python -m src.pipeline --mock                         # 키 없이 전 경로 검증(오프라인)
python -m src.pipeline --probe saramin --role cloud   # P1: 원본 응답 덤프 → probe/
python -m src.pipeline --probe worknet --role public_it
python -m src.pipeline --dry-run --no-llm             # 실제 수집 + 규칙 신호만(LLM 비용 0)
python -m src.pipeline --dry-run                      # 실제 수집 + LLM 판정, 노션 미발행
python -m src.pipeline --init-db --parent-page <ID>   # 최초 1회 노션 DB 생성
python -m src.pipeline --publish                      # 실제 발행(주차 멱등)
python -m pytest tests -q                             # 규칙 회귀 테스트
```

`--role <slug>`로 직무를 하나로 좁힐 수 있다(`cloud`, `public_it`, `sys_ops`, `tech_support`,
`network`). `--week 2026-W33`으로 주차를 고정할 수 있다.

## 현재 상태

| 단계 | 상태 |
|---|---|
| P2 수집·정규화·규칙 신호 | 구현 완료 (`--mock` 통과) |
| P3 LLM 배치 판정 | 구현 완료 (주입 테스트 통과, 실제 Gemini 호출은 키 필요) |
| P4 노션 발행·중복 제거 | 구현 완료 (주입 테스트 통과, 실제 발행은 키 필요) |
| P5 슬랙·스케줄 | 구현 완료 (`weekly.yml`, 수동 dispatch 검증 필요) |
| **P1 소스 필드 확정** | **미완 — 키 발급 후 사용자가 `--probe`를 돌려야 한다** |

### ⚠️ P1이 아직 안 끝났다 (숨기지 않는다)

`src/sources/saramin.py`, `worknet.py`의 `FIELDS` 매핑은 **공개 문서 기준 추정치**이고 실제
응답으로 검증되지 않았다. 그래서 두 클라이언트는 후보 경로를 여러 개 두고 먼저 걸리는 것을 쓰는
방어적 매핑으로 작성돼 있지만, 이는 확정을 대신하지 않는다. 키를 발급한 뒤:

```bash
python -m src.pipeline --probe saramin --role cloud
python -m src.pipeline --probe worknet --role public_it
```

를 돌리면 원본 응답이 `probe/`에 저장되고, 추출 결과와 **비어 있는 필드 목록**이 출력된다.
비는 필드가 있으면 해당 소스의 `FIELDS`를 실제 응답에 맞게 고친다. 워크넷은 엔드포인트가
work.go.kr 직접이냐 data.go.kr 경유냐에 따라 인증 파라미터명·응답 포맷이 달라지므로
`WORKNET_API_URL` / `WORKNET_AUTH_PARAM` / `WORKNET_EXTRA_PARAMS` env로 교정한다.

### ⚠️ 회사 규모는 판정할 수 없다

사람인 검색 API는 사원수/기업규모를 주지 않고, 공고 상세 본문도 주지 않는다(메타데이터만).
따라서 "소기업·오너 1인" 위험은 공고 문구에 명시된 경우(`1인 전산`, `대표 직속` 등)에만 잡히고,
그 외에는 LLM이 `company_size = 판단 불가`를 반환한다. 이 값은 노션 페이지 본문에
"공고에 정보 없음 — 직접 확인 필요"로 그대로 노출된다. **추측으로 메우지 않는다.**

## 파이프라인

```
GitHub Actions cron (목 22:00 UTC = 금 07:00 KST)
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
