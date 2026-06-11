import time
import re
import sys
import os
import json
import hashlib
import random
import socket
import subprocess
import pyperclip
import gspread
from google.oauth2.service_account import Credentials
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
import config
import paths


# ── 프롬프트 관리 (언어별 즐겨찾기 + ID 규칙 자동 주입) ──────────────────────

# 사용자가 편집/추가하는 외부 프롬프트 폴더 (exe 옆 또는 소스 폴더).
PROMPTS_DIR = paths.app_path("prompts")
# exe 안에 번들된 기본 프롬프트 (개발 모드에선 PROMPTS_DIR 과 동일 폴더).
_BUNDLED_PROMPTS_DIR = paths.resource_path("prompts")
# 번들 기본값을 외부로 시드한 시점의 해시 기록 (편집 보존 판정용).
_PROMPT_SEED_FILE = paths.app_path(".prompt_seed.json")


def ensure_external_prompts():
    """exe 실행 시 번들된 기본 프롬프트를 PROMPTS_DIR 로 시드한다.

    3-way 판정으로 사용자가 편집한 파일은 보존하고, 손대지 않은 파일만
    새 기본값으로 갱신한다. (updater 의 프롬프트 보존 철학을 로컬에서 재현)
    개발 모드(번들=외부 동일 폴더)에서는 아무 것도 하지 않는다.
    """
    src, dst = _BUNDLED_PROMPTS_DIR, PROMPTS_DIR
    if os.path.abspath(src) == os.path.abspath(dst) or not os.path.isdir(src):
        return
    os.makedirs(dst, exist_ok=True)
    try:
        with open(_PROMPT_SEED_FILE, "r", encoding="utf-8") as f:
            seed = json.load(f)
    except Exception:
        seed = {}
    changed = False
    for name in os.listdir(src):
        if not name.endswith(".txt"):
            continue
        d = os.path.join(dst, name)
        with open(os.path.join(src, name), "rb") as f:
            bundled = f.read()
        bhash = hashlib.sha256(bundled).hexdigest()
        if not os.path.exists(d):
            with open(d, "wb") as f:
                f.write(bundled)
            seed[name] = bhash
            changed = True
            continue
        with open(d, "rb") as f:
            chash = hashlib.sha256(f.read()).hexdigest()
        if chash == bhash:
            continue  # 이미 최신
        if seed.get(name) == chash:
            # 직전 시드본 그대로 = 사용자가 안 건드림 → 새 기본값으로 갱신
            with open(d, "wb") as f:
                f.write(bundled)
            seed[name] = bhash
            changed = True
        # else: 사용자가 편집했거나 기록 없음 → 보존
    if changed:
        try:
            with open(_PROMPT_SEED_FILE, "w", encoding="utf-8") as f:
                json.dump(seed, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

# UI 라디오에 표시할 순서 (파일명 = 코드, 표시명은 LANG_LABELS 참조)
# 파일명을 ASCII로 둬서 OS/브라우저 어디서도 깨지지 않게 한다.
PROMPT_LANGS = [
    "es", "es_general", "en", "fr", "de",
    "pt", "tr", "id", "zh_cn", "th",
]

# 코드 → 화면 표시용 한글 라벨
LANG_LABELS = {
    "es": "스페인어", "es_general": "스페인어(일반)", "en": "영어",
    "fr": "프랑스어", "de": "독일어", "pt": "포르투갈어",
    "tr": "튀르키어", "id": "인도네시아어", "zh_cn": "중국어(간체)", "th": "태국어",
}

# 코드가 모든 프롬프트 끝에 자동으로 붙이는 행 ID 규칙.
# 프롬프트 파일 자체에는 ID 규칙을 두지 않는다 → 파일은 순수 번역 규칙만 유지하고,
# ID 정합성은 코드가 단일 지점에서 보장한다. (format_batch / parse_response와 짝)
ID_RULE_BLOCK = """

────────────────────────────────
[ 행 식별자(ID) 규칙 — 시스템 필수 (다른 모든 출력 규칙에 우선) ]
────────────────────────────────

- 입력의 각 줄은 맨 앞에 'R숫자' 식별자가 탭(TAB)으로 붙어 제공됩니다.
  예) R12<탭>번역할내용     /     R13<탭>KR<탭>EN<탭>Target
- 출력도 각 줄 맨 앞에 입력과 '완전히 동일한' R숫자를 붙이고, 탭(TAB) 뒤에 번역 결과를 적으십시오.
  예) R12<탭>번역결과
- R숫자 식별자는 번역·변경·삭제하지 말고, 숫자도 절대 바꾸지 마십시오. (그대로 복사)
- 입력 한 줄당 출력도 정확히 한 줄. 줄을 합치거나 쪼개지 마십시오.
- 출력 순서는 입력과 동일하게 유지하되, 매칭 기준은 R숫자입니다.
- R숫자 식별자는 번역 결과물이 아니므로, 번역문 안에 별도로 다시 적지 마십시오.
"""


def list_prompt_langs():
    """prompts 폴더에 실제로 존재하는 언어 목록 반환 (PROMPT_LANGS 순서 우선)."""
    if not os.path.isdir(PROMPTS_DIR):
        return []
    available = {f[:-4] for f in os.listdir(PROMPTS_DIR) if f.endswith(".txt")}
    ordered = [lang for lang in PROMPT_LANGS if lang in available]
    # PROMPT_LANGS에 없지만 폴더엔 있는 파일도 뒤에 덧붙임
    extras = sorted(available - set(PROMPT_LANGS))
    return ordered + extras


def load_prompt(lang):
    """언어 이름으로 prompts/{lang}.txt를 읽고, 끝에 ID 규칙을 자동 주입해 반환.

    파일이 없으면 빈 문자열 + 경고. 반환된 문자열을 config.FIXED_PROMPT로 쓴다.
    """
    if not lang:
        return ""
    path = os.path.join(PROMPTS_DIR, f"{lang}.txt")
    if not os.path.exists(path):
        print(f"⚠️  프롬프트 파일이 없습니다: {path}")
        return ""
    with open(path, "r", encoding="utf-8") as f:
        body = f.read().strip()
    return body + ID_RULE_BLOCK


# ── Google Sheets 연결 ──────────────────────────────────────────────────────

def extract_spreadsheet_id(value):
    """전체 URL 또는 순수 ID 어느 쪽이 들어와도 스프레드시트 ID 만 반환.

    예) https://docs.google.com/spreadsheets/d/<ID>/edit?gid=0 → <ID>
        https://drive.google.com/open?id=<ID>                 → <ID>
        <ID>                                                  → <ID>
    """
    s = (value or "").strip()
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", s)
    if m:
        return m.group(1)
    m = re.search(r"[?&]id=([a-zA-Z0-9-_]+)", s)
    if m:
        return m.group(1)
    return s


def get_sheet():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(paths.app_path("credentials.json"), scopes=scopes)
    client = gspread.authorize(creds)
    sid = extract_spreadsheet_id(config.SPREADSHEET_ID)
    try:
        spreadsheet = client.open_by_key(sid)
    except Exception as e:
        raise RuntimeError(
            "스프레드시트를 열 수 없습니다. 스프레드시트 ID 와, 서비스 계정 이메일에 "
            f"시트 '공유'가 되어 있는지 확인하세요.\n(ID: {sid})\n원인: {e}")
    try:
        return spreadsheet.worksheet(config.SHEET_NAME)
    except Exception:
        try:
            names = ", ".join(ws.title for ws in spreadsheet.worksheets())
        except Exception:
            names = "(목록을 가져오지 못함)"
        raise RuntimeError(
            f"'{config.SHEET_NAME}' 시트(탭)를 찾을 수 없습니다.\n"
            f"설정의 '시트 이름'을 다음 중 하나로 맞춰주세요: {names}")


def is_empty(val):
    """빈 값 또는 nan 여부 확인"""
    v = val.strip().lower()
    return v == "" or v == "nan"


def col_to_idx(letter):
    """열 문자 → 0-based 인덱스 (A→0, B→1, ...)"""
    return ord(letter.upper()) - ord('A')


def get_col_roles():
    """config에서 열 역할 설정 반환 [(col_idx, role), ...] 순서 보장"""
    roles = []
    for col, role_attr in [("A", "COL_A_ROLE"), ("B", "COL_B_ROLE"), ("C", "COL_C_ROLE")]:
        role = getattr(config, role_attr, None)
        if role:
            roles.append((col_to_idx(col), role))
    return roles


def get_pending_rows(sheet):
    """설정된 입력열 기준으로 미번역 행 반환 (nan 포함 공백 처리)"""
    all_values = sheet.get_all_values()
    pending = []
    skipped = 0
    result_idx = col_to_idx(getattr(config, 'RESULT_COL', 'D'))
    col_roles = get_col_roles()
    input_idxs = [idx for idx, _ in col_roles]

    for i, row in enumerate(all_values[config.START_ROW - 1:], start=config.START_ROW):
        # 결과열 값
        d = row[result_idx].strip() if len(row) > result_idx else ""

        # 입력열 값 수집
        vals = {}
        for idx, role in col_roles:
            v = row[idx].strip() if len(row) > idx else ""
            vals[role] = "" if is_empty(v) else v

        # 모든 입력열이 비어있으면 무시
        if not any(vals.values()):
            continue

        if is_empty(d):
            if any(vals.values()):
                # (row_num, source, ref, placeholder) 형태 유지
                pending.append((
                    i,
                    vals.get("source", ""),
                    vals.get("ref", ""),
                    vals.get("placeholder", "")
                ))
        else:
            skipped += 1
    if skipped > 0:
        print(f"  → {skipped}행 건너뜀 (D열 이미 완료)")
    return pending


def log_failure(start_row, end_row, reason):
    """실패한 행 범위를 실패목록.txt에 기록"""
    log_path = paths.app_path("실패목록.txt")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"{start_row}~{end_row}행 — {reason}\n")
    print(f"  📝 실패 기록: {start_row}~{end_row}행 ({reason})")


def write_results(sheet, start_row, results):
    """설정된 결과열에 일괄 기입"""
    result_col = getattr(config, 'RESULT_COL', 'D')
    updates = [
        {"range": f"{result_col}{start_row + idx}", "values": [[val]]}
        for idx, val in enumerate(results)
    ]
    if updates:
        try:
            sheet.batch_update(updates)
            rc = getattr(config, 'RESULT_COL', 'D')
            print(f"  → 스프레드시트 저장 완료 ({rc}{start_row}~{rc}{start_row + len(results) - 1})")
        except Exception as e:
            print(f"  ❌ 스프레드시트 저장 실패: {e}")


def write_status(sheet, row_num, status_text):
    """E열에 상태 텍스트 기입"""
    try:
        sheet.update(f"E{row_num}", [[status_text]])
    except Exception as e:
        print(f"  ❌ 상태 기입 실패: {e}")


# ── Chrome / Selenium ────────────────────────────────────────────────────────

# 표준 Chrome 설치 위치 (자동 탐색용)
_CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]


def find_chrome():
    """Chrome 실행 파일 경로를 반환. 설정값 우선, 없으면 표준 위치 탐색. 못 찾으면 ''."""
    p = (getattr(config, "CHROME_BINARY_PATH", "") or "").strip()
    if p and os.path.exists(p):
        return p
    for c in _CHROME_CANDIDATES:
        if c and os.path.exists(c):
            return c
    return ""


def _is_port_open(port, host="127.0.0.1"):
    """host:port 에 누군가 listen 중이면 True."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def _launch_chrome_debug(binary, port):
    """전용 프로필로 Chrome 을 디버깅 모드로 띄운다. (run_ui.bat 의 앱 내장판)

    일반 Chrome 창은 건드리지 않도록 전용 user-data-dir(chrome-session)을 쓴다.
    """
    session = paths.app_path("chrome-session")
    os.makedirs(session, exist_ok=True)
    args = [
        binary,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={session}",
        "--no-first-run", "--no-default-browser-check",
        "--no-restore-last-session", "--disable-session-crashed-bubble",
        "--disable-infobars",
    ]
    subprocess.Popen(args, close_fds=True)


def ensure_chrome_running(port, timeout=20):
    """디버깅 포트가 안 열려 있으면 Chrome 을 띄우고 열릴 때까지 대기.

    이미 열려 있으면 재사용한다. Chrome 을 못 찾으면 안내 메시지와 함께 예외.
    """
    if _is_port_open(port):
        return True
    binary = find_chrome()
    if not binary:
        raise RuntimeError(
            "Chrome 실행 파일을 찾을 수 없습니다. 설정에서 Chrome 경로를 지정하거나 "
            "최초 설정 마법사를 다시 실행해주세요.")
    _launch_chrome_debug(binary, port)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _is_port_open(port):
            return True
        time.sleep(0.5)
    raise RuntimeError("Chrome 디버깅 포트가 열리지 않았습니다. 잠시 후 다시 시도해주세요.")


def setup_driver():
    options = Options()

    if config.USE_REMOTE_DEBUGGING:
        # 포트가 안 열려 있으면 설정된 Chrome 을 직접 띄워 연결 (exe 자립 실행)
        ensure_chrome_running(config.REMOTE_DEBUGGING_PORT)
        options.add_experimental_option(
            "debuggerAddress", f"127.0.0.1:{config.REMOTE_DEBUGGING_PORT}"
        )
        print(f"  → 크롬에 연결 중 (포트 {config.REMOTE_DEBUGGING_PORT})...")
    else:
        # 프로필 경로로 새 크롬 실행
        binary = find_chrome()
        if binary:
            options.binary_location = binary
        options.add_argument(f"--user-data-dir={config.CHROME_PROFILE_PATH}")
        options.add_argument(f"--profile-directory={config.CHROME_PROFILE_DIR}")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        print("  → 새 크롬 실행 중...")

    driver = webdriver.Chrome(options=options)
    if not config.USE_REMOTE_DEBUGGING:
        driver.maximize_window()
    return driver


def new_conversation(driver):
    """ChatGPT 새 대화 시작"""
    driver.get("https://chatgpt.com/")
    time.sleep(4)
    print("  → 새 대화 페이지 로드 완료")


def send_message(driver, text):
    """ChatGPT 입력창에 텍스트 입력 후 전송"""
    wait = WebDriverWait(driver, 30)

    # 입력창 찾기
    try:
        textarea = wait.until(
            EC.presence_of_element_located((By.ID, "prompt-textarea"))
        )
    except TimeoutException:
        # 대체 셀렉터
        textarea = wait.until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "div[contenteditable='true']")
            )
        )

    textarea.click()
    time.sleep(0.5)

    # 기존 내용 초기화
    textarea.send_keys(Keys.CONTROL + "a")
    textarea.send_keys(Keys.DELETE)
    time.sleep(0.3)

    # JS execCommand로 직접 입력
    # (클립보드 붙여넣기 시 ChatGPT가 파일 첨부로 변환하는 문제 방지)
    driver.execute_script(
        "arguments[0].focus(); document.execCommand('insertText', false, arguments[1]);",
        textarea, text
    )
    time.sleep(0.5)

    # execCommand 실패 시 클립보드 폴백
    current = driver.execute_script("return arguments[0].innerText;", textarea)
    if not current or not current.strip():
        pyperclip.copy(text)
        textarea.send_keys(Keys.CONTROL + "v")
        time.sleep(0.5)

    # 전송 버튼 클릭
    try:
        send_btn = wait.until(EC.element_to_be_clickable(
            (By.CSS_SELECTOR, "button[data-testid='send-button']")
        ))
        send_btn.click()
    except Exception:
        try:
            send_btn = driver.find_element(
                By.CSS_SELECTOR, "button[data-testid='send-button']"
            )
            send_btn.click()
        except NoSuchElementException:
            textarea.send_keys(Keys.RETURN)

    # 전송 후 랜덤 딜레이 (봇 감지 방지)
    time.sleep(random.uniform(1.5, 3.5))
    print("  → 메시지 전송 완료, 응답 대기 중...")


def wait_for_response(driver, timeout=300):
    """ChatGPT 응답 완료까지 대기 — 출현 후 소멸 방식"""
    init_wait     = getattr(config, 'RESPONSE_INIT_WAIT',     2.0)
    poll_interval = getattr(config, 'RESPONSE_POLL_INTERVAL', 0.5)
    done_delay    = getattr(config, 'RESPONSE_DONE_DELAY',    1.0)

    time.sleep(init_wait)

    stop_selectors = [
        "button[data-testid='stop-button']",
        "button[aria-label='Stop streaming']",
        "button[aria-label='Stop generating']",
    ]

    # 1단계: Stop 버튼이 나타날 때까지 대기 (최대 30초)
    appeared = False
    start = time.time()
    while time.time() - start < 30:
        for sel in stop_selectors:
            elems = driver.find_elements(By.CSS_SELECTOR, sel)
            if elems and elems[0].is_displayed():
                appeared = True
                break
        if appeared:
            break
        time.sleep(poll_interval)

    if not appeared:
        time.sleep(5)

    # 2단계: Stop 버튼이 사라질 때까지 대기
    start = time.time()
    while time.time() - start < timeout:
        try:
            found = False
            for sel in stop_selectors:
                elems = driver.find_elements(By.CSS_SELECTOR, sel)
                if elems and elems[0].is_displayed():
                    found = True
                    break
            if not found:
                time.sleep(done_delay)
                print("  → 응답 완료")
                return
        except Exception:
            pass
        time.sleep(poll_interval)

    print("  ⚠️ 응답 완료 감지 타임아웃 — 계속 진행")
    time.sleep(done_delay)


def extract_last_response(driver):
    """마지막 assistant 메시지에서 코드블록 내용 추출 — 다중 폴백"""
    try:
        # 시도 1: data-message-author-role 셀렉터
        messages = driver.find_elements(
            By.CSS_SELECTOR, "[data-message-author-role='assistant']"
        )

        # 시도 2: 대화 메시지 컨테이너
        if not messages:
            messages = driver.find_elements(
                By.CSS_SELECTOR, "[data-message-author-role='assistant'] .markdown"
            )

        # 시도 3: prose 클래스
        if not messages:
            messages = driver.find_elements(
                By.CSS_SELECTOR, ".markdown.prose, div.prose"
            )

        # 시도 4: JavaScript로 직접 추출
        if not messages:
            result = driver.execute_script("""
                const msgs = document.querySelectorAll('[data-message-author-role="assistant"]');
                if (msgs.length === 0) return null;
                const last = msgs[msgs.length - 1];
                const code = last.querySelector('code');
                if (code) return code.innerText;
                return last.innerText;
            """)
            if result:
                return result.strip()
            return None

        last = messages[-1]

        # 코드블록 우선
        code_blocks = last.find_elements(By.TAG_NAME, "code")
        if code_blocks:
            biggest = max(code_blocks, key=lambda x: len(x.text))
            if biggest.text.strip():
                return biggest.text.strip()

        # 코드블록 없으면 전체 텍스트
        text = last.text.strip()
        if text:
            return text

        # 최후: JS fallback
        result = driver.execute_script("""
            const msgs = document.querySelectorAll('[data-message-author-role="assistant"]');
            if (msgs.length === 0) return null;
            const last = msgs[msgs.length - 1];
            const code = last.querySelector('code');
            if (code) return code.innerText;
            return last.innerText;
        """)
        return result.strip() if result else None

    except Exception as e:
        print(f"  ⚠️ 응답 추출 오류: {e}")
        return None


# ── 배치 처리 ─────────────────────────────────────────────────────────────────

def sanitize_cell(text):
    """셀 내부 줄바꿈을 <NL>로 치환 (배치 포맷 보호)"""
    return text.replace("\n", "<NL>").replace("\r", "")


def restore_cell(text):
    """<NL>을 다시 줄바꿈으로 복원"""
    return text.replace("<NL>", "\n")


def group_consecutive_rows(rows):
    """연속된 행 번호끼리 묶어서 그룹 리스트로 반환"""
    if not rows:
        return []
    groups, cur = [], [rows[0]]
    for row in rows[1:]:
        if row[0] == cur[-1][0] + 1:
            cur.append(row)
        else:
            groups.append(cur)
            cur = [row]
    groups.append(cur)
    return groups


def row_id(row_num):
    """행 번호 → 행 ID 문자열 (예: 2 → 'R2')"""
    return f"R{row_num}"


# 응답 줄에서 맨 앞 'R<숫자><탭 또는 공백>' 형태의 ID를 떼어내는 패턴
# 모델이 탭 대신 공백/콜론 등을 넣는 경우까지 관대하게 허용한다.
_ID_PREFIX_RE = re.compile(r'^\s*R(\d+)\s*[\t:.)\-]?\s*')


def format_batch(batch_rows):
    """
    배치를 ChatGPT 입력 형식으로 변환 — 활성화된 열만 포함
    각 줄 맨 앞에 행 ID(R{행번호})를 탭으로 붙여, 응답을 위치가 아니라
    ID로 매칭할 수 있게 한다. 셀 내부 줄바꿈은 <NL>로 치환하여 행 구분 보호.
    """
    col_roles = get_col_roles()
    role_order = [role for _, role in col_roles]

    role_to_key = {"source": 1, "ref": 2, "placeholder": 3}
    lines = []
    for row in batch_rows:
        parts = [row_id(row[0])]  # 맨 앞에 행 ID
        for role in role_order:
            key = role_to_key.get(role)
            if key is not None:
                parts.append(sanitize_cell(row[key]))
        lines.append("\t".join(parts))
    return "\n".join(lines)


def _strip_id(line):
    """응답 한 줄에서 선행 행 ID를 분리. 반환: (행번호 or None, 나머지 텍스트)"""
    m = _ID_PREFIX_RE.match(line)
    if not m:
        return None, line
    return int(m.group(1)), line[m.end():]


def parse_response(response_text, batch_rows):
    """응답 텍스트를 행 ID 기준으로 파싱해, batch_rows 순서대로 정렬된 번역 list를 반환.

    - 각 응답 줄에서 'R<행번호>' ID를 떼어내 {행번호: 번역} 매핑을 만든다.
    - batch_rows 순서대로 각 행의 번역을 꺼내 list를 재구성한다.
      → 응답이 한 줄 밀리거나 순서가 바뀌어도 ID로 제자리를 찾는다.
    - ID가 없는 행(모델이 누락)은 빈 문자열로 채워, 잘못된 번역이 조용히
      기입되는 대신 '빈 칸'으로 드러나게 한다.

    반환: (lines, missing_rows)
      lines        : batch_rows와 같은 길이의 번역 list (누락 행은 "")
      missing_rows : ID 매칭에 실패해 빈 칸으로 남은 행 번호 list
    """
    text = re.sub(r"```[a-z]*\n?", "", response_text)
    text = re.sub(r"```", "", text).strip()

    # ID → 번역 매핑 구성
    id_map = {}
    no_id_lines = []  # ID 없이 들어온 줄 (폴백용)
    for raw in text.split("\n"):
        if not raw.strip():
            continue
        rid, rest = _strip_id(raw)
        if rid is not None:
            id_map[rid] = restore_cell(rest)
        else:
            no_id_lines.append(restore_cell(raw))

    expected_rows = [row[0] for row in batch_rows]

    # ID가 하나도 안 잡혔다면(모델이 ID를 통째로 무시) → 위치 기반 폴백
    if not id_map and no_id_lines:
        print("  ⚠️ 응답에 행 ID가 없음 — 위치 기반 매칭으로 폴백")
        lines = []
        for i in range(len(expected_rows)):
            lines.append(no_id_lines[i] if i < len(no_id_lines) else "")
        missing = [expected_rows[i] for i in range(len(expected_rows))
                   if i >= len(no_id_lines)]
        return lines, missing

    # ID 기반 정렬 재구성
    lines, missing = [], []
    for rnum in expected_rows:
        if rnum in id_map:
            lines.append(id_map[rnum])
        else:
            lines.append("")
            missing.append(rnum)

    if missing:
        print(f"  ⚠️ 응답에서 누락된 행 ID: {missing}")

    return lines, missing


def has_korean(text):
    """텍스트에 한글 포함 여부 확인"""
    import re
    return bool(re.search(r'[가-힣]', text))


def filter_korean_lines(lines):
    """한글 포함된 라인 인덱스 반환"""
    return [i for i, line in enumerate(lines) if has_korean(line)]


# ── 메인 ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 50)
    print("  RO 로컬라이제이션 자동 번역 시작")
    print("=" * 50)

    # Google Sheets 연결
    print("\n[1/3] Google Sheets 연결 중...")
    try:
        sheet = get_sheet()
    except Exception as e:
        print(f"❌ Sheets 연결 실패: {e}")
        sys.exit(1)

    pending_rows = get_pending_rows(sheet)
    if not pending_rows:
        print("처리할 행이 없습니다. (D열이 이미 모두 채워져 있거나 데이터 없음)")
        return

    total = len(pending_rows)
    print(f"✅ 총 {total}행 처리 예정")

    # Chrome 실행
    print("\n[2/3] Chrome 실행 중...")
    try:
        driver = setup_driver()
    except Exception as e:
        print(f"❌ Chrome 실행 실패: {e}")
        sys.exit(1)

    # 번역 루프
    print("\n[3/3] 번역 시작\n")
    processed = 0
    send_count = 0
    groups = group_consecutive_rows(pending_rows)

    try:
        gi = 0
        bi = 0

        while gi < len(groups):
            group = groups[gi]

            # ── 새 대화 시작 조건 ─────────────────────────────
            if send_count == 0 or send_count >= config.MAX_SENDS_PER_CONVERSATION:
                # 새 대화 시 시트 재스캔 → 실패 구멍 수거
                if send_count > 0:
                    print("  → 시트 재스캔 중 (실패 구멍 수거)...")
                    fresh = get_pending_rows(sheet)
                    if fresh:
                        groups = group_consecutive_rows(fresh)
                        total = processed + len(fresh)
                        gi = 0
                        bi = 0
                        group = groups[gi]
                        print(f"  → 재스캔 완료: {len(fresh)}행 / {len(groups)}개 그룹")
                print(f"▶ 새 대화 시작 (누적 처리: {processed}/{total}행)")
                new_conversation(driver)
                send_message(driver, config.FIXED_PROMPT)
                wait_for_response(driver)
                send_count = 0
                print("  → 고정 프롬프트 전송 완료\n")

            # ── 배치 구성: 그룹 크기에 맞게 ──────────────────
            batch = group[bi : bi + config.BATCH_SIZE]
            start_row_num = batch[0][0]
            end_row_num = batch[-1][0]

            if len(group) < config.BATCH_SIZE:
                print(f"  배치 전송: {start_row_num}~{end_row_num}행 ({len(batch)}행, 구멍 그룹)")
            else:
                print(f"  배치 전송: {start_row_num}~{end_row_num}행 ({len(batch)}행)")

            batch_text = format_batch(batch)
            send_message(driver, batch_text)
            wait_for_response(driver)

            # ── 응답 추출 및 기입 (재시도 포함) ──────────────
            response = None
            for attempt in range(1, 4):
                response = extract_last_response(driver)
                if response:
                    break
                if attempt < 3:
                    print(f"  ⚠️ 응답 추출 실패 ({attempt}회) — 2초 후 재시도...")
                    time.sleep(2)

            if response:
                lines, missing = parse_response(response, batch)
                if missing:
                    log_failure(missing[0], missing[-1],
                                f"응답 행 ID 누락 ({len(missing)}행)")

                # ── 한글 감지 및 재번역 ────────────────────────
                korean_idxs = filter_korean_lines(lines)
                if korean_idxs:
                    print(f"  ⚠️ 한글 감지 ({len(korean_idxs)}행) — 재번역 요청 중...")
                    retry_batch = [batch[i] for i in korean_idxs if i < len(batch)]
                    retry_msg = (
                        "아래 항목의 결과값에 한글이 포함되어 있습니다.\n"
                        "한글을 완전히 제거하고 목표 언어로만 재번역하여 "
                        "코드블록으로만 출력하세요.\n"
                        "각 줄 맨 앞의 R숫자 ID는 그대로 두세요.\n\n"
                        + format_batch(retry_batch)
                    )
                    send_message(driver, retry_msg)
                    wait_for_response(driver)
                    retry_response = extract_last_response(driver)
                    if retry_response:
                        # 재시도도 ID 기반 — retry_batch 순서대로 정렬된 결과를 받음
                        retry_lines, _ = parse_response(retry_response, retry_batch)
                        for j, idx in enumerate(korean_idxs):
                            if j < len(retry_lines) and idx < len(lines):
                                if retry_lines[j] and not has_korean(retry_lines[j]):
                                    lines[idx] = retry_lines[j]
                                else:
                                    print(f"  ❌ {start_row_num+idx}행 재번역 후에도 한글 포함 — 원본 유지")
                        print(f"  → 재번역 완료")

                count = len(lines)  # batch와 항상 같은 길이 (누락 행은 빈 칸)
                write_results(sheet, start_row_num, lines)
                processed += count
                print(f"  ✅ {count}행 기입 완료 (누적: {processed}/{total})\n")
            else:
                print(f"  ❌ 응답 추출 최종 실패 — {start_row_num}~{end_row_num}행 건너뜀\n")
                log_failure(start_row_num, end_row_num, "응답 추출 실패")

            send_count += 1
            bi += len(batch)
            if bi >= len(group):
                gi += 1
                bi = 0

            time.sleep(random.uniform(config.DELAY_MIN, config.DELAY_MAX))

    except KeyboardInterrupt:
        print("\n\n⏹️ 사용자 중단 — 지금까지 처리된 결과는 저장됐습니다.")

    except Exception as e:
        print(f"\n❌ 오류 발생: {e}")

    finally:
        print(f"\n{'=' * 50}")
        print(f"  완료: {processed}/{total}행 처리됨")
        print(f"{'=' * 50}")
        input("\nEnter를 누르면 Chrome이 닫힙니다...")
        driver.quit()


if __name__ == "__main__":
    main()