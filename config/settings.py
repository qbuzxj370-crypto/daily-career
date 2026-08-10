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
DATA_GO_KR_KEY = os.environ.get("DATA_GO_KR_KEY", "")
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
# 워크넷(고용24) 채용정보 목록. data.go.kr 경유 URL을 쓸 경우에도 env로 교체 가능.
WORKNET_API_URL = os.environ.get(
    "WORKNET_API_URL", "https://openapi.work.go.kr/opi/opi/opia/wantedApi.do")
HTTP_TIMEOUT = float(os.environ.get("CS_HTTP_TIMEOUT", "15"))

# --- 모델 (Gemini) ---
MODEL = os.environ.get("CS_MODEL", "gemini-2.5-flash")
# 폴백 모델: 기본 모델이 API 오류/검증 실패로 막히면 승계(동일 Gemini SDK, 추상화 없음).
MODEL_FALLBACK = os.environ.get("CS_MODEL_FALLBACK", "gemini-2.5-flash-lite")
MAX_TOKENS = 8000  # 배치(최대 10건) JSON 배열을 담을 여유

# --- 타임존 (KST 하드코딩; cron은 UTC로 환산해 0 22 * * 4) ---
TIMEZONE = ZoneInfo("Asia/Seoul")

# --- 튜닝 상수 ---
EVAL_BATCH_SIZE = 10      # LLM 배치당 공고 수(무료 티어 RPM 회피)
MAX_JOBS_PER_ROLE = 30    # 직무당 수집 상한(프롬프트·비용 팽창 방지)
PER_KEYWORD_FETCH = 20    # 키워드 1개당 소스에서 가져올 최대 건수
SLACK_TOP_N = 5           # 슬랙에 제목까지 싣는 '적합' 공고 수
ROLE_PRIORITY = PRIORITY_SLUGS  # 정본은 config/roles.py

# 판정 텍스트 상한(공고 1건이 프롬프트를 잡아먹지 않도록)
RAW_TEXT_LIMIT = 1200
