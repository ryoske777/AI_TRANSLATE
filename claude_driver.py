"""
Claude.ai 웹 드라이버 — main.py의 ChatGPT 함수들과 동일한 인터페이스로,
claude.ai 웹사이트와 상호작용하는 함수들을 제공한다.
"""

import time
import random
import os
import socket
import subprocess
import pyperclip
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

import config
import paths


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
    """전용 프로필로 Chrome 을 디버깅 모드로 띄운다."""
    session = paths.app_path("chrome-session")
    os.makedirs(session, exist_ok=True)
    args = [
        binary,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={session}",
        "--no-first-run", "--no-default-browser-check",
        "--no-restore-last-session", "--disable-session-crashed-bubble",
        "--disable-infobars",
        "--disable-background-timer-throttling",
        "--disable-renderer-backgrounding",
        "--disable-backgrounding-occluded-windows",
    ]
    subprocess.Popen(args, close_fds=True)


def ensure_chrome_running(port, timeout=20):
    """디버깅 포트가 안 열려 있으면 Chrome 을 띄우고 열릴 때까지 대기."""
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
    """Chrome 드라이버를 설정하고 반환한다."""
    options = Options()

    if config.USE_REMOTE_DEBUGGING:
        ensure_chrome_running(config.REMOTE_DEBUGGING_PORT)
        options.add_experimental_option(
            "debuggerAddress", f"127.0.0.1:{config.REMOTE_DEBUGGING_PORT}"
        )
        print(f"  → 크롬에 연결 중 (포트 {config.REMOTE_DEBUGGING_PORT})...")
    else:
        binary = find_chrome()
        if binary:
            options.binary_location = binary
        options.add_argument(f"--user-data-dir={config.CHROME_PROFILE_PATH}")
        options.add_argument(f"--profile-directory={config.CHROME_PROFILE_DIR}")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        print("  → 새 크롬 실행 중...")

    try:
        driver = webdriver.Chrome(options=options)
    except Exception as e:
        raise RuntimeError(
            "Chrome 드라이버(chromedriver) 시작 실패. 보통 인터넷에서 드라이버를 "
            "자동 설치하지 못할 때 발생합니다. 인터넷 연결을 확인하고 다시 시도하세요.\n"
            f"원인: {e}")
    if not config.USE_REMOTE_DEBUGGING:
        driver.maximize_window()

    try:
        driver.execute_cdp_cmd("Emulation.setFocusEmulationEnabled", {"enabled": True})
    except Exception:
        pass

    return driver


def new_conversation(driver):
    """Claude.ai 새 대화 시작"""
    driver.get("https://claude.ai/")
    time.sleep(4)
    print("  → 새 대화 페이지 로드 완료")


def _insert_text_cdp(driver, textarea, text):
    """CDP(Input.insertText)로 입력창에 텍스트를 넣는다. 성공 시 True."""
    try:
        driver.execute_script(
            """
            const el = arguments[0];
            el.focus();
            const r = document.createRange();
            r.selectNodeContents(el);
            const s = window.getSelection();
            s.removeAllRanges();
            s.addRange(r);
            """,
            textarea,
        )
        driver.execute_cdp_cmd("Input.insertText", {"text": text})
        time.sleep(0.3)
        current = driver.execute_script("return arguments[0].innerText;", textarea)
        return bool(current and current.strip())
    except Exception:
        return False


def _click_send_button(driver, textarea):
    """전송 버튼 클릭. JS 클릭 → Selenium 클릭 → Enter 순서로 시도."""
    # Claude.ai 전송 버튼 셀렉터들
    selectors = [
        "button[data-testid='send-button']",
        "button[aria-label='Send']",
        "button[aria-label='Send message']",
        "div[role='button'][aria-label*='Send']",
    ]

    for sel in selectors:
        try:
            btns = driver.find_elements(By.CSS_SELECTOR, sel)
            if btns:
                driver.execute_script("arguments[0].click();", btns[0])
                return
        except Exception:
            pass

    # Selenium 클릭 시도
    for sel in selectors:
        try:
            btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, sel)))
            btn.click()
            return
        except Exception:
            pass

    # 최후 — Enter
    try:
        textarea.send_keys(Keys.RETURN)
    except Exception:
        pass


def send_message(driver, text):
    """Claude.ai 입력창에 텍스트 입력 후 전송."""
    wait = WebDriverWait(driver, 30)

    # Claude.ai 입력창 찾기
    textarea = None
    selectors = [
        (By.CSS_SELECTOR, "textarea"),
        (By.CSS_SELECTOR, "div[role='textbox'][contenteditable='true']"),
        (By.CSS_SELECTOR, "div[contenteditable='true']"),
        (By.CSS_SELECTOR, "input[type='text']"),
    ]

    for how, sel in selectors:
        try:
            textarea = wait.until(
                EC.presence_of_element_located((how, sel))
            )
            if textarea:
                break
        except TimeoutException:
            continue

    if not textarea:
        raise RuntimeError("Claude.ai 입력창을 찾을 수 없습니다.")

    # ── 1순위: CDP 입력 ─────────────────────────────────────────────
    if _insert_text_cdp(driver, textarea, text):
        time.sleep(0.3)
    else:
        # ── 폴백: 기존 방식 ─────────────────────────────────────────
        textarea.click()
        time.sleep(0.5)
        textarea.send_keys(Keys.CONTROL + "a")
        textarea.send_keys(Keys.DELETE)
        time.sleep(0.3)
        driver.execute_script(
            "arguments[0].focus(); document.execCommand('insertText', false, arguments[1]);",
            textarea, text
        )
        time.sleep(0.5)
        current = driver.execute_script("return arguments[0].innerText;", textarea)
        if not current or not current.strip():
            pyperclip.copy(text)
            textarea.send_keys(Keys.CONTROL + "v")
            time.sleep(0.5)

    # 전송
    _click_send_button(driver, textarea)

    # 전송 후 랜덤 딜레이
    time.sleep(random.uniform(1.5, 3.5))
    print("  → 메시지 전송 완료, 응답 대기 중...")


def wait_for_response(driver, timeout=300, should_stop=None, on_tick=None):
    """Claude.ai 응답 완료까지 대기."""
    init_wait     = getattr(config, 'RESPONSE_INIT_WAIT',     2.0)
    poll_interval = getattr(config, 'RESPONSE_POLL_INTERVAL', 0.5)
    done_delay    = getattr(config, 'RESPONSE_DONE_DELAY',    1.0)

    def _stopped():
        return should_stop is not None and should_stop()

    def _tick(elapsed):
        if on_tick:
            try:
                on_tick(elapsed)
            except Exception:
                pass

    time.sleep(init_wait)
    if _stopped():
        return "STOPPED"

    # Claude.ai의 로딩 지시자들
    stop_selectors = [
        "button[aria-label*='Stop']",
        "button[data-testid='stop-button']",
    ]

    # 1단계: Stop 버튼이 나타날 때까지 대기 (최대 30초)
    appeared = False
    start = time.time()
    while time.time() - start < 30:
        if _stopped():
            return "STOPPED"
        for sel in stop_selectors:
            try:
                elems = driver.find_elements(By.CSS_SELECTOR, sel)
                if elems and elems[0].is_displayed():
                    appeared = True
                    break
            except Exception:
                pass
        if appeared:
            break
        _tick(time.time() - start)
        time.sleep(poll_interval)

    if not appeared:
        time.sleep(5)

    # 2단계: Stop 버튼이 사라질 때까지 대기
    start = time.time()
    while time.time() - start < timeout:
        if _stopped():
            return "STOPPED"
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
        _tick(time.time() - start)
        time.sleep(poll_interval)

    print("  ⚠️ 응답 완료 감지 타임아웃 — 계속 진행")
    time.sleep(done_delay)


def check_logged_in(driver, timeout=10):
    """Claude.ai 입력창이 보이면 로그인된 것으로 판단한다."""
    selectors = [
        (By.CSS_SELECTOR, "textarea"),
        (By.CSS_SELECTOR, "div[role='textbox'][contenteditable='true']"),
        (By.CSS_SELECTOR, "div[contenteditable='true']"),
    ]
    end = time.time() + timeout
    while time.time() < end:
        for how, sel in selectors:
            try:
                elems = driver.find_elements(how, sel)
                if elems and elems[0].is_displayed():
                    return True
            except Exception:
                pass
        time.sleep(0.5)
    return False


def extract_last_response(driver):
    """마지막 assistant 메시지에서 코드블록 내용 추출 — 다중 폴백"""
    try:
        # 시도 1: Claude.ai 메시지 컨테이너
        messages = driver.find_elements(
            By.CSS_SELECTOR, "div[data-testid*='message'], div[data-testid*='response']"
        )

        if not messages:
            messages = driver.find_elements(
                By.CSS_SELECTOR, "div[role='article']"
            )

        if not messages:
            messages = driver.find_elements(
                By.CSS_SELECTOR, ".prose, div.prose, [class*='prose']"
            )

        # 시도 4: JavaScript로 직접 추출
        if not messages:
            result = driver.execute_script("""
                const msgs = document.querySelectorAll('[data-testid*="message"], [data-testid*="response"], div[role="article"]');
                if (msgs.length === 0) return null;
                const last = msgs[msgs.length - 1];
                const code = last.querySelector('code');
                if (code) return code.innerText;
                return last.innerText;
            """)
            if result:
                return result.strip()
            return None

        if not messages:
            return None

        last = messages[-1]

        # 코드블록 우선
        try:
            code_blocks = last.find_elements(By.TAG_NAME, "code")
            if code_blocks:
                biggest = max(code_blocks, key=lambda x: len(x.text))
                if biggest.text.strip():
                    return biggest.text.strip()
        except Exception:
            pass

        # 코드블록 없으면 전체 텍스트
        try:
            text = last.text.strip()
            if text:
                return text
        except Exception:
            pass

        # 최후: JS fallback
        result = driver.execute_script("""
            const msgs = document.querySelectorAll('[data-testid*="message"], [data-testid*="response"], div[role="article"]');
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
