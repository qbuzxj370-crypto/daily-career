# 세팅 가이드

> **순서에 의미가 있다.** 두 관문(Phase 1)은 **키가 하나도 필요 없고**, 통과하지 못하면
> 뒤 단계가 무의미하다. 그래서 키 발급과 노션 설정을 관문 뒤로 미룬다.
>
> 각 Phase 끝에 **확인 기준**이 있다. 그게 통과하면 다음으로 간다.

| Phase | 내용 | 키 필요 | 시간 |
|---|---|---|---|
| 0 | fork · 클론 · 패치 · 오프라인 검증 | 없음 | 10분 |
| 1 | **관문 2개** — 수집 가능한가 / 본문이 오는가 | 없음 | 20분 |
| 2 | 키 발급 · 노션 DB 생성 | 필요 | 40분 |
| 3 | GitHub Secrets · 스케줄 | 필요 | 20분 |

---

## Phase 0 — 코드 올려놓기 (키 없음)

### 0-1. fork

GitHub에서 `kmj20021/daily-career` → **Fork**.

### 0-2. 클론하고 패치 적용

```bash
git clone https://github.com/<내계정>/daily-career.git
cd daily-career

git checkout -b feat/career-fit-setup
git am < ~/Downloads/daily-career-setup.patch     # 커밋 7개가 메시지까지 그대로 적용된다
git push -u origin feat/career-fit-setup

# upstream 개선을 가져오거나 PR을 보낼 통로
git remote add upstream https://github.com/kmj20021/daily-career.git
```

`git am`이 충돌하면 `git am --abort` 후 `git apply daily-career-setup.patch`로 파일만 적용하고
직접 커밋한다(커밋 메시지는 잃지만 내용은 같다).

### 0-3. 파이썬 환경

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

권장 버전은 **3.12**다(CI가 3.12를 쓴다).

> ⚠️ `google-genai` import가 `pydantic_core` 버전 충돌로 깨지는 경우가 있다
> (`ImportError: cannot import name 'from_json'`). 지연 import라 테스트·`--mock`에는
> 영향이 없지만 실제 LLM 호출 전에 `pip install -U pydantic google-genai`가 필요하다.

### 0-4. 오프라인 검증

```bash
python -m pytest tests -q          # 205 passed 가 나와야 한다
python -m src.pipeline --mock      # 수집→판정→렌더 전 경로, 키 0개·네트워크 0회
```

**✅ Phase 0 확인 기준**: `205 passed`, `--mock`이 공고 6건을 판정해 마크다운을 출력한다.

여기서 `205 passed`가 안 나오면 환경 문제이므로 **뒤로 가지 말고** 먼저 해결한다. 이 숫자가
이후 모든 회귀 판단의 기준선이다.

---

## Phase 1 — 관문 2개 (키 없음) ★가장 중요

이 둘이 이 프로젝트의 성립 조건이다. **통과 못 하면 계획을 바꿔야 하므로 키 발급보다 먼저 한다.**

### 관문 1 — GitHub Actions에서 work24에 닿는가? (2분)

수집을 Actions로 돌리기로 했는데, upstream의 CI가 `ConnectTimeout`으로 죽은 전력이 있고
**원인이 미확정**이다(`docs/upstream/ref.md` §19). 러너 IP가 해외 데이터센터라, 차단이
원인이면 매일 실패한다.

1. 푸시된 레포 → **Actions** 탭
2. 왼쪽에서 **`probe-work24 (diagnostic)`** 선택
3. **Run workflow** → `repeat`는 기본값 `3` → 실행
4. 로그의 마지막 블록을 읽는다

| 출력 | 의미 | 할 일 |
|---|---|---|
| `✅ 전부 성공` | 러너 IP 차단 없음 | **계획 그대로 진행** |
| `⛔ 전부 실패` | 러너 IP 차단 | 수집을 **로컬 cron** 또는 self-hosted runner(국내 IP)로 옮긴다 |
| `⚠️ 일부만 실패` | 버스트 차단 | `WORKNET_REQUEST_GAP`을 2~3초로 올린다 |

⛔가 나오면 `.env`에 아무것도 안 넣은 상태에서 **로컬에서 같은 호출이 되는지** 확인한다
(관문 2가 그걸 겸한다). 로컬은 되고 Actions만 안 되면 IP 차단이 확정이다.

### 관문 2 — 상세 페이지에 본문이 오는가? (국내 IP에서)

`직무내용`(자유서술 본문)이 요건 통계와 갭 분석의 **유일한 원재료**다. 이게 안 나오면
프로젝트가 성립하지 않는다.

```bash
python -m src.pipeline --probe-detail K130042609300051
```

공고번호는 아무거나 상관없다. 워크넷 검색 결과에서 공고 하나를 열어 URL의
`wantedAuthNo=` 값을 쓰거나, **상세 URL 전체를 그대로** 넣어도 된다(그게 더 안전하다).

```bash
python -m src.pipeline --probe-detail "https://www.work24.go.kr/wk/a/b/1500/empDetailAuthView.do?wantedAuthNo=...&infoTypeCd=VALIDATION&infoTypeGroup=tb_workinfoworknet"
```

출력에서 볼 것:

```
직무내용      (duty           ): 국고보조사업, 연구과제 정산보고서 서류 검토 …
우대사항      (preferred      ): 전산회계1급, 세무회계(2급)
근로자수      (worker_count   ): 2
...
✓ 본문 1,240자 추출 — 요건 추출의 전제 조건 충족
```

| 결과 | 할 일 |
|---|---|
| `✓ 본문 N자 추출` | **통과.** Phase 2로 |
| `⛔ 직무내용(본문)이 비었다` | `probe/worknet_detail_*.html`을 열어 화면 라벨을 찾고 `src/sources/worknet_detail.py`의 `LABELS`를 실제 문자열로 고친다 (정규식은 안 건드려도 된다) |
| `파싱 실패: … 라벨을 찾지 못했습니다` | 상세 페이지가 아니거나 구조가 바뀐 것. 덤프를 확인 |
| `SourceUnreachable` | 네트워크·차단 문제이지 구조 문제가 아니다 |

> `LABELS`의 문자열은 실제 공고 2건의 **화면**에서 관측했지만 **HTML 구조는 미검증**이다.
> 이 관문이 그걸 확정하는 자리다. 통과하면 `tests/test_worknet_detail.py`의 픽스처를
> 실제 덤프 조각으로 교체하고 모듈 상단의 "HTML 구조 미검증" 경고를 지운다.

**✅ Phase 1 확인 기준**: 관문 1이 ✅ 또는 ⚠️(대응 가능), 관문 2에서 본문이 추출된다.

---

## Phase 2 — 키와 노션 (관문 통과 후에만)

### 2-1. `.env` 만들기

```bash
cp .env.example .env
```

### 2-2. Gemini 키

[aistudio.google.com/apikey](https://aistudio.google.com/apikey) → Create API key →
`.env`의 `GEMINI_API_KEY`에 넣는다. 정상 길이 **53자**.

> upstream이 측정한 "모델당 하루 20요청"은 `gemini-2.5-*` 기준이고 현재 설정은 `3.5`/`3.1`이라
> **실제 한도는 미확인**이다. 이 프로젝트는 선별을 코드가 하므로 LLM 요청이 적어 부딪힐
> 가능성이 낮지만, 429가 나면 그때 실측해서 `docs/ref.md`에 적어둘 것.

### 2-3. 노션 통합 + 부모 페이지 (여기서 제일 많이 막힌다)

1. [notion.so/my-integrations](https://www.notion.so/my-integrations) → **New integration**
   → 이름 짓고 생성 → **Internal Integration Secret** 복사 → `.env`의 `NOTION_API_KEY`.
   정상 길이 **50자**, `ntn_`으로 시작.
2. 노션에서 **부모로 쓸 페이지를 하나 만든다** (예: `커리어`).
3. ★ 그 페이지 우상단 `···` → **Connections**(연결) → 방금 만든 통합을 **추가한다.**

> ⚠️ **3번을 빼먹으면 토큰이 유효해도 `ObjectNotFound`가 난다.** 두 오류를 구분해서 읽어야
> 한다 — `Unauthorized`는 토큰이 틀린 것, `ObjectNotFound`는 **연결을 안 한 것**이다.
> upstream이 여기서 시간을 썼다(`docs/upstream/ref.md` §8).

4. 부모 페이지 URL에서 ID를 뽑는다. `notion.so/커리어-3b84cc12d76a...` 의 끝 32자.

### 2-4. 내 노션 DB 만들기

```bash
python -m src.pipeline --init-db --parent-page <부모페이지ID>
```

출력된 `NOTION_DB_ID`를 `.env`에 넣는다.

> 🚫 **`docs/upstream/ref.md` §2에 적힌 DB ID를 쓰지 말 것.** upstream 작성자의 DB다.
> 같은 문서 §7에 "`--init-db` 실행 금지"라는 주석이 있는데 **그건 upstream 기준**이다.
> 이 fork는 반드시 자기 DB를 새로 만든다.

### 2-5. 사람인 · 슬랙 (선택, 나중에 해도 된다)

- 사람인: [oapi.saramin.co.kr](https://oapi.saramin.co.kr) 이용신청 → 승인 후 access-key.
  **본문을 주지 않으므로** 갭 분석에는 못 쓰고 목록 보강용이다. 미설정이면 자동 비활성.
- 슬랙: Incoming Webhook URL. **URL 자체가 비밀**이다. 먼저 노션만으로 며칠 돌려 보고
  알림이 실제로 필요한지 확인한 뒤 붙이는 것을 권한다.

### 2-6. 실제 수집 확인

```bash
python -m src.pipeline --dry-run --no-llm     # 수집 + 규칙·갭만. LLM 비용 0
python -m src.pipeline --dry-run              # + LLM 판정. 노션 미발행
python -m src.pipeline --publish              # 실제 발행
```

**✅ Phase 2 확인 기준**: `--dry-run --no-llm`이 공고를 수집해 판정까지 낸다.
`--publish` 후 노션 DB에 페이지가 생긴다.

---

## Phase 3 — Secrets와 스케줄

### 3-1. GitHub Secrets

레포 → **Settings → Secrets and variables → Actions → New repository secret**

```
GEMINI_API_KEY        (필수)
NOTION_API_KEY        (필수)
NOTION_DB_ID          (필수)
SARAMIN_ACCESS_KEY    (선택)
SLACK_WEBHOOK_URL     (선택)
```

> ⚠️ **Secrets는 로컬 실행에서 읽히지 않는다.** 러너 안에서만 주입되므로 `.env`와 Secrets
> **양쪽에 각각** 넣어야 한다. 하나만 넣고 "왜 로컬에선 되는데 CI에선 안 되지"로 헤매는 게
> 흔한 사고다.

### 3-2. 스케줄

현재 `weekly.yml`은 upstream의 **주 1회(금 07:19 KST)** 설정이다. 이 프로젝트는
**일별 적재 + 주 1회 처리**로 쪼개기로 했으므로 워크플로도 둘로 나눠야 한다 —
그건 구현(P2·P3) 과정에서 만든다. 그때까지는 `workflow_dispatch`로 수동 실행한다.

cron을 쓸 때 주의: **정각(`:00`)을 피한다.** 전 세계 크론이 몰려 지연·드롭이 잦다.
upstream이 `07:19 KST`를 고른 이유다.

---

## 자주 막히는 곳

| 증상 | 원인 |
|---|---|
| `ObjectNotFound` | 부모 페이지에 통합을 **Connections로 연결하지 않았다** |
| `Unauthorized` | 토큰이 틀렸다. 값 대신 **길이**를 먼저 재라 (노션 50 / Gemini 53 / DB_ID 36) |
| 로컬은 되는데 CI만 실패 | Secrets를 안 넣었거나, work24가 러너 IP를 차단했다(관문 1) |
| `SourceUnreachable` | 서버에 **닿지 못한** 것. `--probe`로 구조를 봐도 아무것도 안 나온다 |
| `SourceError` (파싱) | 200을 받고 내용이 달라진 것. 이때만 `--probe`가 의미 있다 |
| 있는 스킬이 갭으로 나온다 | `config/profile.py`의 스킬 이름 오타. `pytest`가 잡아준다 |
| 워크넷 검색이 전체 목록을 준다 | `searchMode=Y`가 빠졌다. 제목을 읽기 전엔 정상처럼 보인다 |

---

## 세팅 후 바로 할 일

1. **`config/profile.py`의 레벨 확인** — 표의 모든 값이 `[추정]`이다. 특히 **SQL·Linux·AWS**가
   0인지 2인지가 갭 목록과 ROI 순위를 가장 크게 바꾼다. 학교 과제나 토이에서 MySQL을
   붙여봤다면 그건 `LEVEL_PRACTICE(2)`다.
2. **`config/rules.py` 기피 조건 채우기** — `docs/career-plan.md` §3-1의 초안을 코드로 옮기고
   `tests/test_rules.py`도 같이 고친다. hard로 올릴 때마다 **실측으로 잔존 건수를 확인**할 것
   (엄격한 기준이 지원 가능한 공고의 대다수를 지운 전례가 있다: 614 → 72).
3. **`config/roles.py` 직군 교체** — 현재는 upstream의 인프라 5직군이다. `backend` 등이
   없어서 `--role backend`는 `KeyError`가 난다.
4. **친구에게 물어볼 것** — ① 사람인 API 승인받았나(받았으면 `--probe saramin` 덤프를 얻으면
   P1이 끝난다) ② 관문 1이 ⛔로 나왔다면 그쪽 CI도 같은 원인인지 ③ 상세 파서를 upstream에
   PR로 돌려줄지(그쪽의 `company_size = 판단 불가` 한계를 푸는 것이라 서로 이득이다)
