"""환경설정 + 조정 가능한 상수. env는 .env에서 로드(없으면 OS env)."""
from __future__ import annotations
import os
from zoneinfo import ZoneInfo

from config.roles import PRIORITY_SLUGS

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# --- 시크릿/외부 ---
SARAMIN_ACCESS_KEY = os.environ.get("SARAMIN_ACCESS_KEY", "")
# 워크넷은 키를 쓰지 않는다(공개 페이지 파싱). 고용24 OPEN-API는 기업회원 전용이라 개인 발급 불가.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
NOTION_API_KEY = os.environ.get("NOTION_API_KEY", "")
NOTION_DB_ID = os.environ.get("NOTION_DB_ID", "")
# Notion 2025-09 API는 data source 단위로 쿼리/생성. 미설정 시 DB에서 자동 해석(단일 소스 가정).
NOTION_DATA_SOURCE_ID = os.environ.get("NOTION_DATA_SOURCE_ID", "")

# Slack Incoming Webhook(선택). 미설정 시 주간 요약 push 자동 비활성.
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")
SEND_SLACK = bool(SLACK_WEBHOOK_URL)

# --- 소스 엔드포인트 ---
# P1(--probe)에서 실제 응답을 확인한 뒤 어긋나면 env로 즉시 덮어쓸 수 있게 열어 둔다.
SARAMIN_API_URL = os.environ.get(
    "SARAMIN_API_URL", "https://oapi.saramin.co.kr/job-search")
# 워크넷(고용24) 채용정보 — 공개 검색 페이지. OPEN-API는 기업회원 전용이라 개인은 못 쓴다
# (2026-08-10 확인, docs/ref.md §7(1)). 사이트 개편에 대비해 env로 교체 가능하게 둔다.
WORKNET_SEARCH_URL = os.environ.get(
    "WORKNET_SEARCH_URL",
    "https://www.work24.go.kr/wk/a/b/1200/retriveDtlEmpSrchList.do")
HTTP_TIMEOUT = float(os.environ.get("CS_HTTP_TIMEOUT", "15"))
# 연결 수립 타임아웃은 따로 짧게 잡는다. 전체 타임아웃과 같이 두면 **패킷이 드롭되는**
# 상황(방화벽이 SYN을 버리는 경우)에서 요청 하나가 15초씩 서 버린다 — 27개 키워드면
# 재시도까지 얹혀 러너의 20분 예산을 넘긴다. 응답이 느린 것과 닿지 않는 것은 다르다.
HTTP_CONNECT_TIMEOUT = float(os.environ.get("CS_HTTP_CONNECT_TIMEOUT", "8"))
# 일시적 전송 실패(ConnectTimeout/ReadTimeout/ConnectError)와 429·5xx만 재시도한다.
# 4xx는 다시 보내도 같은 답이므로 재시도하지 않는다.
HTTP_RETRIES = int(os.environ.get("CS_HTTP_RETRIES", "2"))       # 최초 시도 외 추가 횟수
HTTP_BACKOFF = float(os.environ.get("CS_HTTP_BACKOFF", "1.5"))   # 1.5s → 3s (+지터)
# 연속 N회 '닿지 않음'이면 그 소스를 이번 실행에서 접는다. IP 차단이라면 남은 수십 번도
# 확실히 같은 결과이고, 타임아웃마다 수십 초를 태우면 러너 예산이 먼저 죽는다.
# 키 미설정으로 소스를 비활성화하는 것과 같은 논리다(src/collector.py).
SOURCE_UNREACHABLE_LIMIT = int(os.environ.get("CS_UNREACHABLE_LIMIT", "3"))

# --- 워크넷 검색 필터 (2026-08-10 실측 확정, docs/ref.md §7) ---
# 파라미터 이름·코드값은 전부 실제 요청으로 확인했다. **문서가 아니라 관측값이다.**
#   경력  careerTypes : N=신입 / E=경력 / Z=관계없음  (복수는 콤마)
#   학력  academicGbn : 00=학력무관 / 03=고졸 / 04=대졸(2~3년) / 05=대졸(4년) …
#   등록일 regDateStdt·regDateEndt : **YYYYMMDD**. 하이픈을 넣으면 결과 0건이 된다.
#          (termSearchGbn=W-1은 화면 버튼 상태일 뿐 서버가 무시한다 — 날짜를 직접 줘야 한다.)
#
# 기본값을 '신입만/전문대졸만'이 아니라 관계없음·학력무관까지 포함시킨 이유:
# 워크넷 공고의 대부분이 '학력무관 · 경력무관'으로 등록돼 있어서, 04/N만 남기면
# 지원 가능한 공고의 대다수가 사라진다(클라우드 키워드 실측: 614 → 04만 72, 00+04 476).
# 엄격하게 좁히려면 WORKNET_ACADEMIC_GBN=04, WORKNET_CAREER_TYPES=N 으로 덮어쓰면 된다.
WORKNET_CAREER_TYPES = os.environ.get("WORKNET_CAREER_TYPES", "N,Z")
WORKNET_ACADEMIC_GBN = os.environ.get("WORKNET_ACADEMIC_GBN", "00,04")
WORKNET_REG_DAYS = int(os.environ.get("WORKNET_REG_DAYS", "7"))  # 등록일: 최근 N일(오늘 포함)
# 워크넷 요청 간 최소 간격(초). 목록 한 건이 ~500KB인데 27개 키워드를 지연 없이 연속으로
# 때리면 버스트로 보인다. 2026-08-28 CI 실행이 ConnectTimeout으로 죽은 원인 후보 중 하나가
# 이것이라, 국내 IP에서는 무해한 수준(총 +30초 미만)으로 간격을 둔다. 0이면 비활성.
WORKNET_REQUEST_GAP = float(os.environ.get("WORKNET_REQUEST_GAP", "1.0"))

# --- 모델 (Gemini) ---
# 무료 티어 할당량은 **모델별로 따로** 센다(모델당 하루 20요청). 그래서 폴백 모델은
# "기본 모델이 하루치를 다 썼을 때 쓰는 예비 할당량" 역할도 겸한다 — 반드시 다른 모델이어야 한다.
#
# ⚠️ 2026-08-11 확인: `gemini-2.5-flash-lite`는 models.list()에는 여전히 뜨지만 실제로 호출하면
#    404 `no longer available to new users`를 돌려준다. **목록에 있다고 쓸 수 있는 게 아니다** —
#    모델을 바꿀 땐 반드시 실제 호출로, 그것도 이 프로젝트의 response_schema로 확인할 것.
#    (`gemini-3.5-flash-lite`는 호출은 되지만 우리 config를 400 INVALID_ARGUMENT로 거부한다.)
#    아래 두 모델은 evaluator.RESPONSE_SCHEMA와 prescreen.RESPONSE_SCHEMA 양쪽으로 실호출 검증됨.
MODEL = os.environ.get("CS_MODEL", "gemini-3.5-flash")
MODEL_FALLBACK = os.environ.get("CS_MODEL_FALLBACK", "gemini-3.1-flash-lite")
MAX_TOKENS = 8000  # 배치(최대 10건) JSON 배열을 담을 여유

# --- 타임존 (KST 하드코딩; cron은 UTC로 환산해 19 22 * * 4) ---
TIMEZONE = ZoneInfo("Asia/Seoul")

# --- 튜닝 상수 ---
EVAL_BATCH_SIZE = 10      # LLM 배치당 공고 수(무료 티어 RPM 회피)
# 제목 프리스크린: 본판정 전에 '제목+회사명'만으로 버릴 것을 버린다(src/prescreen.py).
# 0/false로 끄면 수집된 전부가 본판정과 노션 발행으로 간다.
PRESCREEN = os.environ.get("CS_PRESCREEN", "1").lower() not in ("0", "false", "no", "")
PRESCREEN_BATCH_SIZE = 60   # 제목 한 줄짜리라 본판정보다 훨씬 크게 묶는다
PRESCREEN_MAX_TOKENS = 2000  # 출력이 번호 배열뿐이라 토큰이 거의 안 든다
# 노션 보관 주차 수(이번 주 포함). 이보다 오래된 주차 페이지는 --publish 시작 시 정리.
PURGE_KEEP_WEEKS = int(os.environ.get("CS_PURGE_KEEP_WEEKS", "3"))
MAX_JOBS_PER_ROLE = 30    # 직무당 수집 상한(프롬프트·비용 팽창 방지)
PER_KEYWORD_FETCH = 20    # 키워드 1개당 소스에서 가져올 최대 건수
SLACK_TOP_N = 5           # 슬랙에 제목까지 싣는 '적합' 공고 수
ROLE_PRIORITY = PRIORITY_SLUGS  # 정본은 config/roles.py

# 판정 텍스트 상한(공고 1건이 프롬프트를 잡아먹지 않도록)
RAW_TEXT_LIMIT = 1200
