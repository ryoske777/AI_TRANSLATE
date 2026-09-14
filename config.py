# -*- coding: utf-8 -*-
"""
config.py — 설정 기본값 (단일 진실 원천 아님)

주의:
  - 이 파일은 '초기 기본값'만 정의한다.
  - 실제 정본은 settings.json 이며, main_ui.load_settings() 가 실행 시
    이 모듈의 값을 settings.json 값으로 덮어쓴다. (이건 정상 동작)
  - 따라서 SPREADSHEET_ID 같은 사용자 고유 값은 여기 비워두고,
    UI(설정창) 또는 settings.json 에서 지정한다.
  - credentials.json(구글 서비스 계정 키)은 이 파일과 무관하며,
    repo 에 절대 커밋하지 않는다.
"""

# ── 구글 시트 ────────────────────────────────────────────────────────────────
SPREADSHEET_ID = ""        # 사용자별 시트 ID — UI/settings.json 에서 지정
SHEET_NAME     = "시트1"    # 작업 대상 워크시트 이름
START_ROW      = 2          # 데이터 시작 행 (1행은 보통 헤더)

# ── 열 역할 ──────────────────────────────────────────────────────────────────
#   값: "source" | "ref" | "placeholder" | None
#   하드코딩하지 않고 작업마다 UI 에서 지정한다. 아래는 흔한 기본 배치.
COL_A_ROLE = "source"       # A열: 원본(보통 한글)
COL_B_ROLE = "placeholder"  # B열: 플레이스홀더 포함 번역대상
COL_C_ROLE = None           # C열: 미사용
RESULT_COL = "D"            # 번역 결과 기입 열

#   특이사항(비고) 기입 열 — 비우면 '결과열 바로 다음 열'을 자동으로 쓴다.
#   기본 배치는 결과 D → 특이사항 E. 연속 번역처럼 결과열이 언어마다 다를 때도
#   각 결과열에 붙은 특이사항 열을 자동으로 따라간다.
NOTE_COL = ""

# ── 배치/속도 ────────────────────────────────────────────────────────────────
BATCH_SIZE                 = 30    # 한 번에 보낼 줄 수 (자동 분량 꺼짐일 때 사용)
AUTO_BATCH_SIZE            = False # True면 BATCH_SIZE 무시, 글자 수 기준으로 행 수 자동 결정
AUTO_BATCH_CHAR_BUDGET     = 2500  # 자동 분량: 메시지 1건당 목표 최대 글자 수
AUTO_BATCH_MAX_ROWS        = 30    # 자동 분량: 짧은 행이어도 1회에 이 행 수를 넘지 않음
MAX_SENDS_PER_CONVERSATION = 10    # 대화 1개당 최대 전송 횟수 (넘으면 새 대화)
DELAY_MIN                  = 3.0   # 배치 간 최소 대기(초)
DELAY_MAX                  = 15.0  # 배치 간 최대 대기(초)

# ── 응답 대기 타이밍 ─────────────────────────────────────────────────────────
RESPONSE_INIT_WAIT     = 2.0   # 전송 후 응답 시작 전 초기 대기
RESPONSE_POLL_INTERVAL = 1.0   # 응답 완료 폴링 간격
RESPONSE_DONE_DELAY    = 2.0   # 응답 완료 판정 후 추가 안정화 대기

# ── 크롬 구동 ────────────────────────────────────────────────────────────────
#   run_ui.bat 가 전용 프로필 크롬을 디버깅 모드(포트 9222)로 띄우고,
#   main.py 는 USE_REMOTE_DEBUGGING=True 일 때 그 인스턴스에 붙는다.
USE_REMOTE_DEBUGGING  = True
REMOTE_DEBUGGING_PORT = 9222

#   Chrome 실행 파일 경로. 비우면 표준 설치 위치에서 자동 탐색한다.
#   exe 사용자는 최초 설정 마법사 또는 설정창에서 지정한다.
CHROME_BINARY_PATH = ""         # 예: r"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"

#   USE_REMOTE_DEBUGGING=False 일 때만 사용하는 폴백 프로필 경로.
CHROME_PROFILE_PATH = ""        # 예: r"C:\\Users\\이름\\AppData\\Local\\Google\\Chrome\\User Data"
CHROME_PROFILE_DIR  = "Default"

# ── 번역 동작 ────────────────────────────────────────────────────────────────
PRESERVE_PLACEHOLDERS = True       # 플레이스홀더 보존 + 재시도
MASK_PLACEHOLDERS     = True       # «T:내용» → «T:번호» 로 숨겨 전송, 응답에서 복원 (훼손 원천 차단)
AI_MODE               = "chatgpt"  # "chatgpt" | "claude"
PROMPT_LANG           = "es"       # prompts/{PROMPT_LANG}.txt — 존재하는 값으로 폴백됨

# ── 작업 모드 ────────────────────────────────────────────────────────────────
#   "translate"       : 기존 번역 모드
#   "review_glossary" : 용어집 검수 (단어 + 카테고리, 동음이의어/게임 용어 자연스러움 검수)
#   "review_general"  : 일반 검수 (문장 단위, 원문→대상 언어 번역 품질 검수)
WORK_MODE       = "translate"
REVIEW_SRC_LANG = "ko"   # 검수 원문 언어 (ko 포함 가능)
REVIEW_TGT_LANG = "es"   # 검수 대상(번역문) 언어

#   검수 크로스체크: '수정' 판정이 나온 행을 같은 대화에서 한 번 더 재검토시켜,
#   다른 언어 규칙·취향을 근거로 한 잘못된 수정 제안을 OK로 걸러낸다.
REVIEW_CROSS_CHECK = True

#   모드별 열 역할 프리셋 — 모드를 전환하면 아래 값이 COL_*_ROLE/RESULT_COL 에 적용된다.
#   실제 정본은 settings.json 의 MODE_COL_ROLES (설정창에서 편집 시 현재 모드에 저장됨).
MODE_COL_ROLES = {
    "translate":       {"COL_A_ROLE": "source", "COL_B_ROLE": "placeholder", "COL_C_ROLE": None,     "RESULT_COL": "D"},
    "review_glossary": {"COL_A_ROLE": "source", "COL_B_ROLE": "category",    "COL_C_ROLE": "review", "RESULT_COL": "D"},
    "review_general":  {"COL_A_ROLE": "source", "COL_B_ROLE": "review",      "COL_C_ROLE": None,     "RESULT_COL": "D"},
}

# ── 용어집 (Glossary) ────────────────────────────────────────────────────────
#   같은 스프레드시트의 한 탭에 든 용어집을 읽어 두 가지에 쓴다.
#     ① «T:...» 플레이스홀더 내용을 그 언어의 공식 용어로 확정 치환 (AI 무관)
#     ② 배치에 등장하는 용어만 뽑아 "이 번역을 쓰라"는 지시문을 프롬프트에 첨부
#   필요한 열: ko-KR(열쇠), 각 언어 열(en-US/zh-CN/th-TH/es-ES/...),
#              aliases, match_mode, protect_level, priority, status
GLOSSARY_ENABLED = False      # 용어집 사용 여부 (탭 이름을 지정하면 켜진다)
GLOSSARY_TAB     = ""         # 용어집이 든 탭 이름 (예: "glossary_master")

#   프롬프트 지시문에 한 배치당 실을 최대 용어 수 (HARD → SOFT → HINT 순으로 채움)
GLOSSARY_MAX_TERMS = 60


# ── 연속 번역 (여러 언어를 순서대로 자동 실행) ───────────────────────────────
#   메인 화면 🗂 버튼에서 편집한다. 실제 정본은 settings.json 의 SEQ_JOBS.
#   각 단계: {"lang": 언어코드, "mode": "translate"|"copy",
#             "result_col": 결과열, "note_col": 특이사항열, "enabled": bool}
#     - mode "translate" : prompts/{lang}.txt 로 AI 번역
#     - mode "copy"      : AI 호출 없이 입력열 값을 결과열로 그대로 복사 (ko-KR 용)
SEQ_JOBS = []

#   '원본 복사' 단계가 가져올 입력열 역할.
#     "auto"        : 플레이스홀더(번역대상) 열이 있으면 그 열, 없으면 원본 열
#     "placeholder" / "source" / "ref" : 해당 역할 열을 직접 지정
SEQ_COPY_FROM = "auto"


# ── 런타임 주입 ──────────────────────────────────────────────────────────────
#   load_prompt(PROMPT_LANG) 결과가 실행 시 여기에 채워진다. 직접 편집 불필요.
FIXED_PROMPT = ""
