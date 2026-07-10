"""
RO 로컬라이제이션 자동 번역 툴 — UI 버전 (main_ui.py)
main.py의 모든 기능을 그대로 유지하며 GUI로 감싼 버전
"""

import customtkinter as ctk
import tkinter as tk
from tkinter import scrolledtext, messagebox, filedialog
import threading
import queue
import os
import re
import sys
from datetime import datetime

# ── main.py의 모든 함수를 그대로 import ─────────────────────────────────────
# 번들 리소스 폴더를 import 경로에 추가 (exe 면 _MEIPASS, 개발이면 소스 폴더).
import paths
sys.path.insert(0, paths.resource_dir())

from main import (
    get_sheet, get_pending_rows, log_failure, write_results, write_status,
    setup_driver, new_conversation, send_message, wait_for_response,
    extract_last_response, sanitize_cell, restore_cell,
    group_consecutive_rows, format_batch, format_review_batch, parse_response,
    auto_batch_count,
    is_empty, col_to_idx,
    list_prompt_langs, load_prompt, load_review_prompt, ensure_external_prompts,
    parse_review_verdict, write_review_notes, next_col_letter,
    find_chrome,
    extract_spreadsheet_id, get_service_account_email, test_connection,
    check_logged_in, PROMPTS_DIR, LANG_LABELS,
    WORK_MODE_LABELS, REVIEW_MODES, REVIEW_LANGS,
)
import config

# exe 실행 시 번들된 기본 프롬프트를 외부 폴더로 시드 (편집 보존).
ensure_external_prompts()

# config에 PROMPT_LANG 속성이 없으면 기본값 주입 (첫 번째 사용 가능한 언어)
if not hasattr(config, "PROMPT_LANG"):
    _langs = list_prompt_langs()
    config.PROMPT_LANG = _langs[0] if _langs else ""

# config에 PRESERVE_PLACEHOLDERS 속성이 없으면 기본값(True) 주입
if not hasattr(config, "PRESERVE_PLACEHOLDERS"):
    config.PRESERVE_PLACEHOLDERS = True

# AI 모드: "chatgpt"(기본) 또는 "claude" — 어느 웹을 셀레니움으로 구동할지 결정
if not hasattr(config, "AI_MODE"):
    config.AI_MODE = "chatgpt"

# 작업 모드: "translate"(번역) / "review_glossary"(용어집 검수) / "review_general"(일반 검수)
if not hasattr(config, "WORK_MODE"):
    config.WORK_MODE = "translate"
if not hasattr(config, "REVIEW_SRC_LANG"):
    config.REVIEW_SRC_LANG = "ko"
if not hasattr(config, "REVIEW_TGT_LANG"):
    config.REVIEW_TGT_LANG = "es"


# ── 모드별 열 역할 프리셋 ────────────────────────────────────────────────────
# 번역과 검수는 시트 구성이 다르므로(검수엔 '검수대상'·'카테고리' 열이 필요),
# 모드마다 열 역할을 따로 기억해 두고 모드를 전환하면 자동으로 적용한다.
# 정본은 config.MODE_COL_ROLES (settings.json 에 저장) — COL_*_ROLE 은 현재 모드의 사본.

DEFAULT_MODE_COLS = {
    "translate":       {"COL_A_ROLE": "source", "COL_B_ROLE": "placeholder", "COL_C_ROLE": None,     "RESULT_COL": "D"},
    "review_glossary": {"COL_A_ROLE": "source", "COL_B_ROLE": "category",    "COL_C_ROLE": "review", "RESULT_COL": "D"},
    "review_general":  {"COL_A_ROLE": "source", "COL_B_ROLE": "review",      "COL_C_ROLE": None,     "RESULT_COL": "D"},
}


def _mode_presets():
    """config.MODE_COL_ROLES 를 항상 완전한 형태(모든 모드·모든 키)로 보정해 반환."""
    presets = getattr(config, "MODE_COL_ROLES", None)
    if not isinstance(presets, dict):
        presets = {}
    for mode, defaults in DEFAULT_MODE_COLS.items():
        cur = presets.get(mode)
        if not isinstance(cur, dict):
            cur = {}
        merged = dict(defaults)
        for k in defaults:
            if k in cur:
                merged[k] = cur[k]
        presets[mode] = merged
    config.MODE_COL_ROLES = presets
    return presets


def _current_mode():
    mode = getattr(config, "WORK_MODE", "translate")
    return mode if mode in DEFAULT_MODE_COLS else "translate"


def store_mode_columns():
    """현재 config 의 열 역할(COL_*_ROLE/RESULT_COL)을 현재 모드 프리셋에 기록."""
    presets = _mode_presets()
    presets[_current_mode()] = {
        "COL_A_ROLE": getattr(config, "COL_A_ROLE", None),
        "COL_B_ROLE": getattr(config, "COL_B_ROLE", None),
        "COL_C_ROLE": getattr(config, "COL_C_ROLE", None),
        "RESULT_COL": getattr(config, "RESULT_COL", "D"),
    }


def apply_mode_columns():
    """현재 모드의 열 역할 프리셋을 config(COL_*_ROLE/RESULT_COL)에 적용."""
    presets = _mode_presets()
    p = presets[_current_mode()]
    config.COL_A_ROLE = p.get("COL_A_ROLE")
    config.COL_B_ROLE = p.get("COL_B_ROLE")
    config.COL_C_ROLE = p.get("COL_C_ROLE")
    config.RESULT_COL = (p.get("RESULT_COL") or "D")

# 사용자 데이터는 exe 옆(app_dir)에 둔다 — 번들 폴더가 아니라.
BASE_DIR = paths.app_dir()
FAIL_LOG     = paths.app_path("실패목록.txt")
SETTINGS_FILE = paths.app_path("settings.json")


# ── 플레이스홀더 검증 유틸 ───────────────────────────────────────────────────
# RO 로컬라이제이션 플레이스홀더 형식: «T:내용»
#   - «, »는 길리메(U+00AB, U+00BB)
#   - T: 가 표시자
#   - 한 문장에 여러 번 등장 가능, 중첩은 없음
PLACEHOLDER_PATTERN = re.compile(r'«T:[^«»]*»')


def extract_placeholders(text):
    """텍스트에서 플레이스홀더를 모두 추출 (출현 순서 유지)"""
    if not text:
        return []
    return PLACEHOLDER_PATTERN.findall(text)


def check_placeholder_match(source, translated):
    """원본과 번역의 플레이스홀더 다중집합이 일치하는지 검사.
    원본에 플레이스홀더가 없으면 True (검증 대상 아님)."""
    if not source:
        return True
    src = extract_placeholders(source)
    if not src:
        return True
    tgt = extract_placeholders(translated or "")
    return sorted(src) == sorted(tgt)


def filter_placeholder_mismatch(sources, translations):
    """플레이스홀더가 불일치한 행의 인덱스 리스트"""
    out = []
    for i, src in enumerate(sources):
        tgt = translations[i] if i < len(translations) else ""
        if not check_placeholder_match(src, tgt):
            out.append(i)
    return out


def get_placeholder_col_letter():
    """COL_A/B/C_ROLE 중 'placeholder'로 지정된 컬럼 문자(A/B/C) 반환. 없으면 None."""
    for col_letter, role_attr in [("A", "COL_A_ROLE"),
                                  ("B", "COL_B_ROLE"),
                                  ("C", "COL_C_ROLE")]:
        if getattr(config, role_attr, None) == "placeholder":
            return col_letter
    return None


def get_placeholder_sources(sheet, start_row, count, col_letter):
    """시트에서 플레이스홀더 컬럼 값을 직접 읽어옴 (한 번에 범위 가져오기)"""
    if not col_letter:
        return [""] * count
    range_addr = f"{col_letter}{start_row}:{col_letter}{start_row + count - 1}"
    try:
        result = sheet.get(range_addr)
        values = []
        for row in result:
            values.append(row[0] if row and len(row) > 0 else "")
        while len(values) < count:
            values.append("")
        return values[:count]
    except Exception:
        return [""] * count


# E열 상태 문구
PH_MISMATCH_MARK = "플레이스홀더 불일치"
KOREAN_MARK = "한글 포함"
# 자동으로 관리하는 표시들(아래 목록에 있는 값만 자동 정리/제거 대상. 사용자가 직접 적은 메모는 보존)
MANAGED_MARKS = (PH_MISMATCH_MARK, KOREAN_MARK)


def _clear_cells(sheet, ranges):
    """셀을 '진짜 빈 셀'로 비운다. (빈 문자열 기입이 아니라 값 자체를 삭제)"""
    if not ranges:
        return
    try:
        sheet.batch_clear(ranges)  # values:batchClear → 셀 값 완전 삭제
    except Exception:
        # batch_clear 미지원/실패 시 빈 문자열로 대체 (셀에 빈 값이 남을 수 있음)
        try:
            sheet.batch_update([{"range": r, "values": [[""]]} for r in ranges])
        except Exception as e:
            print(f"  ❌ E열 정리 실패: {e}")


def reconcile_status(sheet, start_row, lines, sources=None):
    """배치 전체의 E열 상태(한글 포함 / 플레이스홀더 불일치)를 최종 결과 기준으로 정리한다.

    각 행의 실제 번역 결과(lines)를 다시 검사해서:
      - 플레이스홀더 불일치 → E열에 '플레이스홀더 불일치'
      - (불일치 아님) 한글 포함 → E열에 '한글 포함'
      - 둘 다 정상     → 자동 표시(MANAGED_MARKS)가 남아있으면 셀을 완전히 비움
    사용자가 직접 적은 다른 메모는 건드리지 않는다.
    sources(플레이스홀더 컬럼 값)가 None이면 플레이스홀더 검사는 건너뛰고 한글만 본다.

    현재 E열을 1회 읽고, 기입이 필요한 셀은 batch_update로, 비울 셀은 batch_clear로 처리한다.
    반환: (mismatch_rows, korean_rows, cleared_rows) — 각각 행 번호 리스트
    """
    from main import has_korean

    count = len(lines)
    if count <= 0:
        return [], [], []

    end_row = start_row + count - 1
    try:
        cur = sheet.get(f"E{start_row}:E{end_row}")
    except Exception:
        cur = []

    def e_at(i):
        if i < len(cur) and cur[i] and len(cur[i]) > 0:
            return (cur[i][0] or "").strip()
        return ""

    ph_mismatch = set()
    if sources:
        ph_mismatch = set(filter_placeholder_mismatch(sources[:count], lines[:count]))

    updates, clears = [], []
    mismatch_rows, korean_rows, cleared_rows = [], [], []

    for i in range(count):
        row_num = start_row + i
        e_val = e_at(i)

        if i in ph_mismatch:
            mismatch_rows.append(row_num)
            desired = PH_MISMATCH_MARK
        elif has_korean(lines[i] if i < len(lines) else ""):
            korean_rows.append(row_num)
            desired = KOREAN_MARK
        else:
            desired = ""

        if desired:
            # 빈칸이거나 우리가 관리하는 표시일 때만 갱신 (사용자 메모 보존)
            if e_val != desired and (e_val == "" or e_val in MANAGED_MARKS):
                updates.append({"range": f"E{row_num}", "values": [[desired]]})
        else:
            # 정상 → 자동 표시가 남아있으면 셀을 완전히 비움
            if e_val in MANAGED_MARKS:
                clears.append(f"E{row_num}")
                cleared_rows.append(row_num)

    if updates:
        try:
            sheet.batch_update(updates)
        except Exception as e:
            print(f"  ❌ E열 상태 기입 실패: {e}")
    _clear_cells(sheet, clears)

    return mismatch_rows, korean_rows, cleared_rows


def cross_check_flagged_rows(sheet):
    """시트 전체에서 E열에 자동 표시(한글 포함 / 플레이스홀더 불일치)가 있는 행을
    현재 C/D열 내용으로 다시 검증한다. 실제로 정상인 경우에만 그 표시를 지운다.
    (새 대화 시작 시 1회 스윕 — 이전 세션의 묵은 표시나 수동 수정분을 정리)

    - '한글 포함'        : D(결과)열에 한글이 없으면 표시 제거
    - '플레이스홀더 불일치' : 플레이스홀더 원본열과 D열의 «T:...» 가 일치하면 표시 제거
                           (플레이스홀더 원본열을 알 수 없으면 검증 불가 → 그대로 둠)
    사용자가 직접 적은 다른 메모는 건드리지 않는다.

    반환(dict): {
        "ok": bool,                # 시트 읽기 성공 여부
        "error": str|None,         # 실패 사유
        "ph_col": 'A'/'B'/'C'|None,# 사용한 플레이스홀더 원본열
        "cleared": [행번호...],     # 재검증 결과 정상 → 표시 지운 행
        "kept_ph": [행번호...],     # 여전히 플레이스홀더 불일치라 유지한 행
        "kept_ko": [행번호...],     # 여전히 한글 포함이라 유지한 행
        "kept_unverifiable": [행번호...],  # 원본열을 몰라 검증 못 한 행
    }
    """
    from main import has_korean

    result = {
        "ok": True, "error": None, "ph_col": None,
        "cleared": [], "kept_ph": [], "kept_ko": [], "kept_unverifiable": [],
    }

    try:
        all_values = sheet.get_all_values()
    except Exception as e:
        result["ok"] = False
        result["error"] = str(e)
        return result

    result_idx = col_to_idx(getattr(config, "RESULT_COL", "D"))
    e_idx = col_to_idx("E")
    ph_col = get_placeholder_col_letter()                 # 'A'/'B'/'C' 또는 None
    ph_idx = col_to_idx(ph_col) if ph_col else None
    start = getattr(config, "START_ROW", 1)
    result["ph_col"] = ph_col

    clears = []
    for i, row in enumerate(all_values[start - 1:], start=start):
        e_val = row[e_idx].strip() if len(row) > e_idx else ""
        if e_val not in MANAGED_MARKS:
            continue
        d_val = row[result_idx] if len(row) > result_idx else ""

        if e_val == KOREAN_MARK:
            if not has_korean(d_val):
                clears.append(f"E{i}")
                result["cleared"].append(i)
            else:
                result["kept_ko"].append(i)
        elif e_val == PH_MISMATCH_MARK:
            if ph_idx is None:
                result["kept_unverifiable"].append(i)  # 원본열을 모름 → 검증 불가
                continue
            src_val = row[ph_idx] if len(row) > ph_idx else ""
            if check_placeholder_match(src_val, d_val):
                clears.append(f"E{i}")
                result["cleared"].append(i)
            else:
                result["kept_ph"].append(i)

    if clears:
        _clear_cells(sheet, clears)

    return result


# ── 번역 엔진 (스레드) ────────────────────────────────────────────────────────

class TranslationWorker(threading.Thread):
    def __init__(self, log_q, done_callback):
        super().__init__(daemon=True)
        self.log_q = log_q
        self.done_callback = done_callback
        self.stop_flag = False
        self.pause_flag = False
        self.force_new_conv = False  # 재시작 시 새 대화 강제
        self._pause_event = __import__('threading').Event()
        self._pause_event.set()  # 초기엔 일시정지 아님

    def log(self, msg, tag="info"):
        now = datetime.now().strftime("%H:%M:%S")
        self.log_q.put(("log", f"[{now}] {msg}", tag))

    def progress(self, cur, total):
        self.log_q.put(("progress", (cur, total)))

    def status(self, text, color="#9ece6a"):
        self.log_q.put(("status", (text, color)))

    def waiting(self, msg):
        self.log_q.put(("waiting", msg))

    def wait_text(self, msg):
        """대기 중 베이스 문구만 갱신(점 애니메이션/상태 유지). 진행 경과 표시용."""
        self.log_q.put(("waiting_text", msg))

    def done_waiting(self):
        self.log_q.put(("done_waiting", None))

    def pause(self):
        self.pause_flag = True
        self._pause_event.clear()
        self.log("일시정지됨 — 현재 배치 완료 후 대기합니다.", "warn")

    def resume(self):
        """재시작 시 새 대화 강제 플래그 설정 후 재개"""
        self.force_new_conv = True
        self.pause_flag = False
        self._pause_event.set()
        self.log("재시작됨 — 새 대화부터 시작합니다.", "success")

    def run(self):
        # ── AI 모드별 드라이버 함수 선택 ─────────────────────────────
        # config.AI_MODE에 따라 ChatGPT(main.py) 또는 Claude(claude_driver.py)
        # 함수들을 로컬 이름으로 바인딩한다. 이 5개 이름은 아래 코드 어디서 부르든
        # 같은 시그니처라 본문은 그대로 동작한다.
        ai_mode = getattr(config, "AI_MODE", "chatgpt").lower()
        if ai_mode == "claude":
            from claude_driver import (
                setup_driver, new_conversation, send_message,
                wait_for_response, extract_last_response,
            )
            self.log("AI 모드: Claude (claude.ai 웹)", "info")
        else:
            from main import (
                setup_driver, new_conversation, send_message,
                wait_for_response, extract_last_response,
            )
            self.log("AI 모드: ChatGPT (chatgpt.com 웹)", "info")

        # ── 작업 모드 (번역/검수) — 실행 시작 시점에 한 번 확정 ─────────
        work_mode = _current_mode()
        is_review = work_mode in REVIEW_MODES
        work_word = "검수" if is_review else "번역"
        fmt_batch = format_review_batch if is_review else format_batch
        if is_review:
            src = getattr(config, "REVIEW_SRC_LANG", "ko")
            tgt = getattr(config, "REVIEW_TGT_LANG", "es")
            self.log(
                f"작업 모드: {WORK_MODE_LABELS.get(work_mode, work_mode)} "
                f"({LANG_LABELS.get(src, src)} → {LANG_LABELS.get(tgt, tgt)})",
                "info")
        else:
            self.log("작업 모드: 번역", "info")

        processed = 0
        total = 0
        driver = None
        try:
            # 이번 실행의 결과만 요약에 보이도록 이전 실패 기록을 초기화한다
            try:
                if os.path.exists(FAIL_LOG):
                    os.remove(FAIL_LOG)
            except Exception:
                pass
            self.status("Google Sheets 연결 중...", "#e0af68")
            self.log("Google Sheets 연결 중...")
            sheet = get_sheet()

            pending_rows = get_pending_rows(sheet)
            if not pending_rows:
                self.log(f"{work_word} 대상 행이 없습니다.", "warn")
                self.log_q.put(("empty", None))
                self.done_callback(0, 0, [])
                return

            total = len(pending_rows)
            self.log(f"총 {total}행 처리 예정", "success")
            self.progress(0, total)

            self.status("Chrome 연결 중...", "#e0af68")
            self.log("Chrome에 연결 중...")
            driver = None
            for attempt in range(1, 6):
                import time as _t
                try:
                    driver = setup_driver()
                    break
                except Exception as e:
                    detail = str(e).replace("\n", " ")[:200]
                    if attempt < 5:
                        self.log(f"Chrome 연결 시도 {attempt}/5 실패: {detail}", "warn")
                        _t.sleep(2)
                    else:
                        raise Exception(f"Chrome 연결 실패 (5회 시도). {detail}")
            self.log("Chrome 연결 완료", "success")

            # ── ChatGPT 로그인 사전 확인 (ChatGPT 모드만) ──────────────────
            # 미로그인 상태면 5분 대기 후 실패하지 않고 즉시 안내한다.
            if ai_mode != "claude":
                self.waiting("ChatGPT 로그인 확인 중")
                new_conversation(driver)
                logged_in = check_logged_in(driver, timeout=12)
                self.done_waiting()
                if self.stop_flag:
                    return
                if not logged_in:
                    raise Exception(
                        "ChatGPT에 로그인되어 있지 않은 것 같습니다. "
                        "열린 Chrome 창에서 chatgpt.com 에 로그인한 뒤 다시 RUN 하세요.")

            self.status(f"{work_word} 진행 중...", "#9ece6a")
            self.send_count = 0
            groups = group_consecutive_rows(pending_rows)
            import math as _math
            auto_batch = bool(getattr(config, "AUTO_BATCH_SIZE", False))

            def plan_batches(gs):
                """앞으로 전송될 배치 수 추정 (진행 표시용)."""
                if not auto_batch:
                    return sum(_math.ceil(len(g) / max(1, config.BATCH_SIZE)) for g in gs)
                n = 0
                for g in gs:
                    i = 0
                    while i < len(g):
                        i += auto_batch_count(g, i, fmt_batch)
                        n += 1
                return n

            total_batches = plan_batches(groups)
            if auto_batch:
                self.log(
                    f"1회 번역 분량: 자동 — 셀 글자 수 기준으로 행 수를 배치마다 결정합니다 "
                    f"(예상 배치 {total_batches}개)", "info")
            batch_no = 0
            gi = 0
            bi = 0

            while gi < len(groups) and not self.stop_flag:
                group = groups[gi]

                if self.send_count == 0 or self.send_count >= config.MAX_SENDS_PER_CONVERSATION or self.force_new_conv:
                    # ── E열 특이사항 재검증 스윕 (번역 모드 전용) ─────
                    # 한글 포함/플레이스홀더 불일치 표시를 현재 C/D 기준으로 다시 보고,
                    # 실제로 정상이면 표시만 지운다. (무엇을 발견/검증했는지 로그로 노출)
                    # 검수 모드는 E열 자동 표시를 쓰지 않으므로 건너뛴다.
                    if not is_review:
                        self.waiting("E열 특이사항 재검증 중")
                        cc = cross_check_flagged_rows(sheet)
                        self.done_waiting()

                        if not cc["ok"]:
                            self.log(f"⚠️ E열 재검증 실패 — 시트 읽기 오류로 건너뜀: {cc['error']}", "error")
                        else:
                            cleared = cc["cleared"]
                            kept_ph = cc["kept_ph"]
                            kept_ko = cc["kept_ko"]
                            kept_unv = cc["kept_unverifiable"]
                            found = len(cleared) + len(kept_ph) + len(kept_ko) + len(kept_unv)

                            if found == 0:
                                self.log("E열 재검증: 특이사항 행을 찾지 못함 (검사 대상 0건)", "info")
                            else:
                                self.log(
                                    f"E열 재검증: 특이사항 {found}행 발견 "
                                    f"→ 정리 {len(cleared)} / 유지 {len(kept_ph) + len(kept_ko) + len(kept_unv)}",
                                    "info"
                                )

                                def _preview(rows, limit=40):
                                    head = ", ".join(str(r) for r in rows[:limit])
                                    return head + ("" if len(rows) <= limit else f" 외 {len(rows) - limit}행")

                                if cleared:
                                    self.log(f"  🧹 정리(이제 정상): {_preview(cleared)}행", "success")
                                if kept_ph:
                                    self.log(f"  📌 유지(여전히 플레이스홀더 불일치): {_preview(kept_ph)}행", "warn")
                                if kept_ko:
                                    self.log(f"  📌 유지(여전히 한글 포함): {_preview(kept_ko)}행", "warn")
                                if kept_unv:
                                    self.log(
                                        f"  ⚠️ 검증 불가(플레이스홀더 원본열 미설정): {_preview(kept_unv)}행",
                                        "warn"
                                    )
                            if cc["ph_col"] is None:
                                self.log("  ↳ 참고: 플레이스홀더 원본열이 설정돼 있지 않아 불일치 행은 검증하지 못했습니다.", "warn")

                    if self.send_count > 0:
                        self.log("시트 재스캔 중 (실패 구멍 수거)...")
                        fresh = get_pending_rows(sheet)
                        if fresh:
                            groups = group_consecutive_rows(fresh)
                            total = processed + len(fresh)
                            gi = 0
                            bi = 0
                            group = groups[gi]
                            total_batches = batch_no + plan_batches(groups)
                            self.log(f"재스캔 완료: {len(fresh)}행 / {len(groups)}개 그룹")
                    self.log(f"새 대화 시작 (누적: {processed}/{total}행)")
                    self.waiting("새 ChatGPT 페이지 로딩 중")
                    new_conversation(driver)
                    self.done_waiting()
                    self.log("페이지 로드 완료", "info")
                    self.waiting("고정 프롬프트 전송 중")
                    send_message(driver, config.FIXED_PROMPT)
                    wait_for_response(driver, should_stop=lambda: self.stop_flag)
                    self.done_waiting()
                    self.send_count = 0
                    self.force_new_conv = False
                    self.log("고정 프롬프트 전송 완료", "success")

                if auto_batch:
                    n_rows = auto_batch_count(group, bi, fmt_batch)
                else:
                    n_rows = config.BATCH_SIZE
                batch = group[bi: bi + n_rows]
                s_row, e_row = batch[0][0], batch[-1][0]
                batch_no += 1
                if auto_batch:
                    label = " · 자동 분량"
                else:
                    label = " (구멍 그룹)" if len(group) < config.BATCH_SIZE else ""
                self.log(f"배치 전송: {s_row}~{e_row}행 ({len(batch)}행{label})")
                self.waiting(f"{s_row}~{e_row}행 {work_word} 요청 중 · 배치 {batch_no}/{total_batches}")
                send_message(driver, fmt_batch(batch))
                self.done_waiting()
                if self.stop_flag:
                    break
                self.waiting(f"ChatGPT 응답 대기 중 · 배치 {batch_no}/{total_batches}")
                _base = f"ChatGPT 응답 대기 중 · 배치 {batch_no}/{total_batches}"
                wait_for_response(
                    driver,
                    should_stop=lambda: self.stop_flag,
                    on_tick=lambda e: self.wait_text(f"{_base} · {int(e)}초"))
                self.done_waiting()
                if self.stop_flag:
                    break

                response = None
                for attempt in range(1, 4):
                    response = extract_last_response(driver)
                    if response:
                        break
                    if attempt < 3:
                        self.log(f"응답 추출 실패 ({attempt}회) — 재시도...", "warn")
                        import time as _t; _t.sleep(2)

                if response:
                    lines, missing = parse_response(response, batch)
                    if missing:
                        self.log(f"⚠️ 응답 행 ID 누락 {len(missing)}행 (빈 칸 유지): {missing}", "warn")
                        log_failure(missing[0], missing[-1],
                                    f"응답 행 ID 누락 ({len(missing)}행)")
                    ph_sources = None  # 플레이스홀더 컬럼 값 (검증 대상일 때만 채워짐)

                    # ── 한글 감지 → 1회 즉시 재시도 (번역 모드 전용) ──
                    # 검수 모드는 결과에 한국어 사유가 포함되는 것이 정상이므로 검사하지 않는다.
                    from main import has_korean, filter_korean_lines
                    korean_idxs = [] if is_review else filter_korean_lines(lines)
                    if korean_idxs:
                        self.log(f"⚠️ 한글 감지 ({len(korean_idxs)}행) — 재번역 시도...", "warn")
                        retry_batch = [batch[i] for i in korean_idxs if i < len(batch)]
                        retry_msg = (
                            "아래 항목에 한글이 포함되어 있습니다.\n"
                            "한글 없이 목표 언어로만 재번역하여 코드블록으로 출력하세요.\n"
                            "각 줄 맨 앞의 R숫자 ID는 그대로 두세요.\n\n"
                            + format_batch(retry_batch)
                        )
                        self.waiting("한글 포함 행 재번역 중")
                        send_message(driver, retry_msg)
                        wait_for_response(driver, should_stop=lambda: self.stop_flag)
                        self.done_waiting()
                        retry_resp = extract_last_response(driver)
                        if retry_resp:
                            retry_lines, _ = parse_response(retry_resp, retry_batch)
                            for j, idx in enumerate(korean_idxs):
                                if j < len(retry_lines) and idx < len(lines):
                                    if retry_lines[j]:
                                        lines[idx] = retry_lines[j]
                        # (E열 '한글 포함' 표시/정리는 아래 reconcile_status에서 일괄 처리)

                    # ── 플레이스홀더 검증 → 1회 즉시 재시도 (번역 모드 전용) ──
                    # 검수 모드의 결과는 번역문이 아니라 판정 텍스트라 검증 대상이 아니다.
                    if not is_review and getattr(config, "PRESERVE_PLACEHOLDERS", True):
                        ph_col = get_placeholder_col_letter()
                        if ph_col:
                            ph_sources = get_placeholder_sources(sheet, s_row, len(batch), ph_col)
                            ph_idxs = filter_placeholder_mismatch(ph_sources, lines)
                            if ph_idxs:
                                self.log(f"⚠️ 플레이스홀더 불일치 감지 ({len(ph_idxs)}행) — 재번역 시도...", "warn")

                                # 어떤 행에 어떤 플레이스홀더가 필요한지 명시
                                hint_lines = []
                                for idx in ph_idxs:
                                    if idx < len(ph_sources):
                                        phs = extract_placeholders(ph_sources[idx])
                                        if phs:
                                            phs_str = " ".join(phs)
                                            hint_lines.append(f"  · 행 {s_row + idx}: {phs_str}")

                                retry_batch = [batch[i] for i in ph_idxs if i < len(batch)]
                                retry_msg = (
                                    "아래 항목의 플레이스홀더가 원본과 다릅니다.\n"
                                    "«T:...» 형식의 플레이스홀더는 절대 번역·변형·삭제하지 말고,\n"
                                    "원본과 동일한 형태·동일한 개수 그대로 유지하세요.\n"
                                    "꺽쇠(« ») 안의 내용(T: 다음의 본문)도 원본과 완전히 같아야 합니다.\n"
                                    "위치만 자연스럽게 옮기는 것은 허용됩니다.\n"
                                    "각 줄 맨 앞의 R숫자 ID는 그대로 두세요.\n\n"
                                    + ("필수 유지 토큰:\n" + "\n".join(hint_lines) + "\n\n"
                                       if hint_lines else "")
                                    + format_batch(retry_batch)
                                )
                                self.waiting("플레이스홀더 불일치 행 재번역 중")
                                send_message(driver, retry_msg)
                                wait_for_response(driver, should_stop=lambda: self.stop_flag)
                                self.done_waiting()
                                retry_resp = extract_last_response(driver)
                                if retry_resp:
                                    retry_lines, _ = parse_response(retry_resp, retry_batch)
                                    for j, idx in enumerate(ph_idxs):
                                        if j < len(retry_lines) and idx < len(lines):
                                            if retry_lines[j]:
                                                lines[idx] = retry_lines[j]

                    # ── E열 상태 최종 정리 (번역 모드 전용) ───────────
                    # 최종 결과(lines)를 다시 검사해 불일치/한글은 표시하고,
                    # 정상이 된 행에 남아있던 자동 표시는 셀을 완전히 비운다.
                    # 검수 모드 결과(OK/수정 제안)에는 해당 없음.
                    if not is_review:
                        mismatch_rows, korean_rows, cleared_rows = reconcile_status(
                            sheet, s_row, lines, ph_sources
                        )
                        for r in mismatch_rows:
                            self.log(f"📝 {r}행 → 플레이스홀더 불일치, E열: 플레이스홀더 불일치", "warn")
                        for r in korean_rows:
                            self.log(f"📝 {r}행 → 한글 포함, E열: 한글 포함", "warn")
                        for r in cleared_rows:
                            self.log(f"🧹 {r}행 → 정상, E열 표시 제거", "success")

                    # ── 검수 모드: 판정 파싱 → 결과열엔 최종 단어, 비고열엔 판정 ──
                    # OK        → 결과열: 기존 번역 그대로 / 비고열: OK
                    # 수정: X…  → 결과열: 수정안 X만     / 비고열: '수정: X | 사유: …' 전체
                    if is_review:
                        finals, notes = [], []
                        for i, ln in enumerate(lines):
                            orig = batch[i][5] if i < len(batch) and len(batch[i]) > 5 else ""
                            f_val, n_val = parse_review_verdict(ln, orig)
                            finals.append(f_val)
                            notes.append(n_val)

                        # ── 크로스체크: '수정' 판정을 판정 원칙 기준으로 재검토 ──
                        # 다른 언어 표기 규칙(예: 독일어 von 소문자)이나 취향을 근거로 한
                        # 잘못된 수정 제안을 같은 대화에서 한 번 더 걸러 OK로 정정한다.
                        fix_idxs = [i for i, (f_val, n_val) in enumerate(zip(finals, notes))
                                    if n_val and n_val != "OK" and f_val]
                        if fix_idxs and getattr(config, "REVIEW_CROSS_CHECK", True) and not self.stop_flag:
                            self.log(f"🔁 수정 판정 {len(fix_idxs)}건 크로스체크 중...", "info")
                            cc_batch = [batch[i] for i in fix_idxs]
                            cc_lines = format_review_batch(cc_batch).split("\n")
                            body = "\n".join(
                                f"{line}\t현재판정={notes[i]}"
                                for line, i in zip(cc_lines, fix_idxs))
                            cc_msg = (
                                "아래는 방금 당신이 내린 '수정' 판정들입니다. [판정 원칙]에 따라 재검토하십시오.\n"
                                "- 사유가 대상 언어의 규칙이 아니라 다른 언어의 표기 관습이거나 단순 취향이면 OK로 정정하십시오.\n"
                                "- 사유가 대상 언어 기준으로 타당하면 같은 판정을 그대로 다시 출력하십시오.\n"
                                "각 줄 맨 앞의 R숫자 ID는 그대로 두고, 각 행마다 'OK' 또는\n"
                                "'수정: <제안> | 사유: <한국어>' 형식으로만 코드블록에 출력하십시오.\n\n"
                                + body)
                            self.waiting("수정 판정 크로스체크 중")
                            send_message(driver, cc_msg)
                            wait_for_response(driver, should_stop=lambda: self.stop_flag)
                            self.done_waiting()
                            cc_resp = extract_last_response(driver)
                            if cc_resp:
                                cc_verdicts, _ = parse_response(cc_resp, cc_batch)
                                reverted = []
                                for j, i in enumerate(fix_idxs):
                                    v = cc_verdicts[j] if j < len(cc_verdicts) else ""
                                    if not v:
                                        continue  # 재검토 응답 누락 → 1차 판정 유지
                                    orig = batch[i][5] if len(batch[i]) > 5 else ""
                                    f2, n2 = parse_review_verdict(v, orig)
                                    if n2 == "OK":
                                        finals[i], notes[i] = orig, "OK"
                                        reverted.append(s_row + i)
                                    elif f2:
                                        finals[i], notes[i] = f2, n2
                                if reverted:
                                    self.log(f"  ↩️ 크로스체크로 OK 정정: {reverted}", "success")

                        issue_rows = [s_row + i for i, n in enumerate(notes)
                                      if n and n != "OK"]
                        if issue_rows:
                            head = ", ".join(str(r) for r in issue_rows[:30])
                            more = "" if len(issue_rows) <= 30 else f" 외 {len(issue_rows) - 30}행"
                            self.log(f"🔎 수정 제안 {len(issue_rows)}행: {head}{more}", "warn")
                        else:
                            self.log("🔎 이 배치는 전부 OK", "success")
                        unparsed = [s_row + i for i, (f_val, n_val)
                                    in enumerate(zip(finals, notes)) if n_val and not f_val]
                        if unparsed:
                            self.log(f"⚠️ 판정 형식을 해석하지 못한 행(비고만 기입, 재실행 시 재검수): {unparsed}", "warn")

                        count = len(finals)
                        write_results(sheet, s_row, finals)
                        write_review_notes(sheet, s_row, notes)
                    else:
                        count = len(lines)  # batch와 항상 같은 길이 (누락 행은 빈 칸)
                        write_results(sheet, s_row, lines)
                    processed += count
                    self.progress(processed, total)
                    self.log(f"✅ {count}행 기입 완료 (누적: {processed}/{total})", "success")
                else:
                    self.log(f"❌ 응답 추출 실패 — {s_row}~{e_row}행 건너뜀", "error")
                    log_failure(s_row, e_row, "응답 추출 실패")

                self.send_count += 1
                bi += len(batch)
                if bi >= len(group):
                    gi += 1
                    bi = 0

                import time as _t
                import random as _r
                # STOP 즉시 반응: 긴 딜레이를 0.2초 단위로 쪼개 중지 플래그를 자주 확인
                delay = _r.uniform(config.DELAY_MIN, config.DELAY_MAX)
                waited = 0.0
                while waited < delay and not self.stop_flag:
                    _t.sleep(min(0.2, delay - waited))
                    waited += 0.2
                if self.stop_flag:
                    break

                # 일시정지 대기 (정지 시 즉시 빠져나오도록 stop()이 이벤트도 set)
                self._pause_event.wait()
                if self.stop_flag:
                    break

            if self.stop_flag:
                self.log(f"⏹ 중지 — {processed}행 처리됨", "warn")
                self.status("중지됨", "#e0af68")
            else:
                self.log(f"🎉 완료! 총 {processed}행 처리됨", "success")
                self.status("완료", "#9ece6a")

        except Exception as e:
            err = str(e)
            if "invalid session id" in err or "browser has closed" in err:
                msg = "Chrome 연결이 끊겼습니다. Chrome을 다시 실행 후 번역 시작을 눌러주세요."
            elif "cannot connect to chrome" in err or "session not created" in err:
                msg = "Chrome에 연결할 수 없습니다. Chrome이 디버그 모드로 실행 중인지 확인해주세요."
            elif "element not interactable" in err or "element click intercepted" in err:
                msg = "ChatGPT 페이지가 아직 로딩 중입니다. 잠시 후 다시 시도해주세요."
            elif "timeout" in err.lower():
                msg = "ChatGPT 응답 시간이 초과됐습니다. 네트워크 상태를 확인해주세요."
            elif "credentials" in err.lower() or "spreadsheet" in err.lower():
                msg = "Google Sheets 연결 실패. credentials.json 또는 스프레드시트 ID를 확인해주세요."
            elif "no such element" in err.lower():
                msg = "ChatGPT UI가 변경됐을 수 있습니다. 잠시 후 다시 시도해주세요."
            else:
                msg = err.split("\n")[0][:120]
            self.log(f"❌ {msg}", "error")
            self.status("오류", "#f7768e")

        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass
            fail_lines = []
            if os.path.exists(FAIL_LOG):
                with open(FAIL_LOG, "r", encoding="utf-8") as f:
                    fail_lines = [l.strip() for l in f if l.strip()]
            self.done_callback(processed, total, fail_lines)


# ── 설정 다이얼로그 ──────────────────────────────────────────────────────────

def clean_release_notes(notes):
    """업데이트 팝업용 릴리스 노트 정리.

    - GitHub URL 전부 제거
    - 'by @사용자' 표기 제거
    - 'Full Changelog' 줄, 'What's Changed' 머리말 줄 제거
    - 마크다운 머리표(##, *)를 보기 좋게 정리
    """
    out = []
    for line in (notes or "").splitlines():
        if "Full Changelog" in line or "What's Changed" in line:
            continue
        line = re.sub(r"\bby @\S+", "", line)              # by @user 제거
        line = re.sub(r"\bin\s+https?://\S+", "", line)    # 'in <url>' 꼬리 제거
        line = re.sub(r"https?://\S+", "", line)           # 남은 URL 제거
        line = re.sub(r"^\s*#+\s*", "", line)              # 마크다운 헤더(##) 제거
        line = re.sub(r"^\s*[\*\-]\s+", "• ", line)        # 글머리표 * → •
        line = line.rstrip(" \t-·")
        out.append(line)
    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


class Tooltip:
    """위젯에 마우스를 올리면 설명 풍선을 띄우는 간단한 툴팁."""

    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip = None
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)

    def _show(self, _=None):
        if self.tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 18
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        tk.Label(self.tip, text=self.text, justify="left",
                 bg="#2d3748", fg="#ffffff", font=("", 9),
                 padx=8, pady=6, wraplength=320,
                 relief="solid", borderwidth=1).pack()

    def _hide(self, _=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None


def add_info_icon(parent, row, column, tip_text):
    """지정 위치에 작은 정보(!) 아이콘을 놓고 마우스오버 설명을 단다."""
    icon = ctk.CTkLabel(parent, text=" ⓘ", width=18,
                        text_color="#3182ce",
                        font=ctk.CTkFont(size=13, weight="bold"))
    icon.grid(row=row, column=column, padx=(4, 0), sticky="w")
    Tooltip(icon, tip_text)
    return icon


def validate_config_values(d):
    """설정값 검증. 문제가 있으면 한국어 오류 메시지(str), 없으면 None 반환.

    d 는 {필드명: 값} 딕셔너리. 저장 직전 SettingsDialog/SetupWizard 에서 공용 사용.
    숫자 변환 오류도 여기서 잡아 친절한 메시지로 돌려준다.
    """
    def _num(key, label, cast, minv=None, maxv=None):
        if key not in d:
            return None
        try:
            v = cast(d[key])
        except (TypeError, ValueError):
            return f"'{label}' 값이 올바른 숫자가 아닙니다."
        if minv is not None and v < minv:
            return f"'{label}' 값은 {minv} 이상이어야 합니다."
        if maxv is not None and v > maxv:
            return f"'{label}' 값은 {maxv} 이하여야 합니다."
        return None

    checks = [
        ("BATCH_SIZE", "1회 번역 분량(시트 행)", int, 1, None),
        ("MAX_SENDS_PER_CONVERSATION", "AI 대화창 전송 횟수", int, 1, None),
        ("START_ROW", "시작 행 번호", int, 1, None),
        ("DELAY_MIN", "딜레이 최소", float, 0, None),
        ("DELAY_MAX", "딜레이 최대", float, 0, None),
        ("RESPONSE_INIT_WAIT", "응답 감지 시작 대기", float, 0, None),
        ("RESPONSE_POLL_INTERVAL", "응답 폴링 간격", float, 0, None),
        ("RESPONSE_DONE_DELAY", "응답 완료 후 대기", float, 0, None),
        ("REMOTE_DEBUGGING_PORT", "디버그 포트", int, 1, 65535),
    ]
    for args in checks:
        err = _num(*args)
        if err:
            return err

    if "DELAY_MIN" in d and "DELAY_MAX" in d:
        try:
            if float(d["DELAY_MAX"]) < float(d["DELAY_MIN"]):
                return "'딜레이 최대'는 '딜레이 최소'보다 크거나 같아야 합니다."
        except (TypeError, ValueError):
            pass

    if "RESULT_COL" in d:
        rc = str(d["RESULT_COL"]).strip()
        if not re.fullmatch(r"[A-Za-z]+", rc):
            return "'번역 결과 기입'은 열 문자만 입력하세요. (예: A, B, C, D, AA)"

    return None


def run_connection_test(parent, button, spreadsheet=None, sheet_name=None):
    """입력한 설정으로 시트 연결을 백그라운드에서 점검하고 결과를 팝업으로 보여준다.

    저장 전이라도 현재 입력값으로 바로 확인할 수 있게, 전달된 값을 잠시 config 에
    반영해 test_connection()을 호출한 뒤 원복한다. (실제 저장은 '저장/완료'가 담당)
    """
    saved = (config.SPREADSHEET_ID, getattr(config, "SHEET_NAME", ""))
    if spreadsheet is not None:
        config.SPREADSHEET_ID = extract_spreadsheet_id(spreadsheet)
    if sheet_name is not None and str(sheet_name).strip():
        config.SHEET_NAME = str(sheet_name).strip()
    try:
        button.configure(state="disabled", text="테스트 중...")
    except Exception:
        pass

    def worker():
        ok, msg = test_connection()

        def show():
            try:
                button.configure(state="normal", text="🔌 연결 테스트")
            except Exception:
                pass
            config.SPREADSHEET_ID, config.SHEET_NAME = saved  # 저장 전이므로 원복
            if ok:
                messagebox.showinfo("연결 테스트", msg, parent=parent)
            else:
                messagebox.showerror("연결 테스트 실패", msg, parent=parent)

        parent.after(0, show)

    threading.Thread(target=worker, daemon=True).start()


def make_share_help(parent, clipboard_widget):
    """스프레드시트 공유 안내 + 서비스 계정 이메일 표시 + 복사 버튼을 만든다.

    반환: (frame, set_email)  — set_email(email) 으로 이메일 표시를 갱신할 수 있다.
    """
    box = ctk.CTkFrame(parent, fg_color="#eef6ff", corner_radius=10)
    ctk.CTkLabel(box, text="⚠️  스프레드시트 공유가 안 되어 있으면 열리지 않습니다",
                 text_color="#2b6cb0",
                 font=ctk.CTkFont(size=12, weight="bold")).pack(
        anchor="w", padx=12, pady=(10, 2))
    ctk.CTkLabel(
        box, justify="left", text_color="#4a5568", font=ctk.CTkFont(size=11),
        text=("방법 1) 아래 '서비스 계정 이메일'을 복사해, 스프레드시트 우상단 [공유]에\n"
              "          붙여넣고 '편집자'로 추가하세요. (가장 안전)\n"
              "방법 2) 스프레드시트 [공유] → '링크가 있는 모든 사용자'를 '편집자'로 변경. (간단)")
        ).pack(anchor="w", padx=12, pady=(0, 6))
    row = ctk.CTkFrame(box, fg_color="transparent")
    row.pack(fill="x", padx=12, pady=(0, 10))
    ctk.CTkLabel(row, text="서비스 계정 이메일", width=110, anchor="w").pack(side="left")
    email_var = tk.StringVar(value="(credentials.json 지정 후 표시됩니다)")
    ctk.CTkEntry(row, textvariable=email_var, width=300).pack(side="left")

    def _copy():
        v = email_var.get().strip()
        if v and "@" in v:
            clipboard_widget.clipboard_clear()
            clipboard_widget.clipboard_append(v)

    ctk.CTkButton(row, text="복사", width=56, command=_copy).pack(side="left", padx=6)

    def set_email(email):
        email_var.set(email if email else "(credentials.json 이 없습니다 — 먼저 지정하세요)")

    return box, set_email


class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("설정")
        self.geometry("500x600")
        self.resizable(True, True)
        self.grab_set()
        self.after(50, self.lift)
        self._build()

    def _field(self, parent, label, value, row, wide=False, tip=None, return_entry=False):
        ctk.CTkLabel(parent, text=label, anchor="w").grid(
            row=row, column=0, padx=(0, 12), pady=6, sticky="w")
        var = tk.StringVar(value=str(value))
        w = 320 if wide else 200
        entry = ctk.CTkEntry(parent, textvariable=var, width=w)
        entry.grid(row=row, column=1, pady=6, sticky="w")
        if tip:
            add_info_icon(parent, row, 2, tip)
        return (var, entry) if return_entry else var

    def _toggle_auto_batch(self):
        """자동 분량 체크 상태에 따라 '1회 번역 분량' 숫자 입력을 활성/비활성."""
        state = "disabled" if self.v_auto_batch.get() else "normal"
        self.e_batch.configure(state=state)

    def _build(self):
        scroll = ctk.CTkScrollableFrame(self)
        scroll.pack(fill="both", expand=True, padx=16, pady=(16, 0))

        # 스프레드시트
        ctk.CTkLabel(scroll, text="📊  스프레드시트",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(
            anchor="w", pady=(0, 6))
        f1 = ctk.CTkFrame(scroll, fg_color="transparent")
        f1.pack(fill="x")
        self.v_id    = self._field(f1, "스프레드시트 주소", config.SPREADSHEET_ID, 0, True)
        self.v_sheet = self._field(f1, "시트(탭) 이름",    config.SHEET_NAME,      1)
        self.v_start = self._field(f1, "시작 행 번호",     config.START_ROW,       2)
        ctk.CTkLabel(scroll, justify="left", text_color="#888",
                     font=ctk.CTkFont(size=11),
                     text=("· 주소: 브라우저의 스프레드시트 주소(URL)를 그대로 붙여넣으면 됩니다.\n"
                           "· 시트(탭) 이름: 화면 아래쪽 탭 이름과 똑같이 입력하세요. (예: 시트1)\n"
                           "· 시작 행 번호: 번역을 시작할 행. 보통 1행은 제목이라 2 입니다.")
                     ).pack(anchor="w", padx=4, pady=(2, 6))

        share_box, set_email = make_share_help(scroll, self)
        share_box.pack(fill="x", pady=(0, 4))
        set_email(get_service_account_email())

        ctk.CTkFrame(scroll, height=1, fg_color="#3a3a4a").pack(
            fill="x", pady=10)

        # 번역 설정
        ctk.CTkLabel(scroll, text="⚙️  번역 설정",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(
            anchor="w", pady=(0, 6))
        # 자동 분량 — 켜면 아래 '1회 번역 분량' 숫자 입력이 비활성화된다
        auto_f = ctk.CTkFrame(scroll, fg_color="transparent")
        auto_f.pack(fill="x", pady=(0, 2))
        self.v_auto_batch = tk.BooleanVar(
            value=bool(getattr(config, "AUTO_BATCH_SIZE", False)))
        ctk.CTkCheckBox(
            auto_f,
            text="자동 — 1회 번역 분량을 글자 수 기준으로 자동 결정",
            variable=self.v_auto_batch,
            command=self._toggle_auto_batch,
        ).pack(anchor="w", padx=4)
        ctk.CTkLabel(
            auto_f,
            text="  · 보낼 셀의 글자 수를 미리 계산해, AI가 한 번에 무리 없이 처리할 만큼만 행 수를 정합니다.\n"
                 "  · 긴 설명문은 1~10행, 짧은 용어는 최대 30행까지 자동 조절. 켜면 아래 숫자 입력은 무시됩니다.",
            text_color="#888", font=ctk.CTkFont(size=11), justify="left",
        ).pack(anchor="w", padx=24, pady=(2, 2))

        f2 = ctk.CTkFrame(scroll, fg_color="transparent")
        f2.pack(fill="x")
        self.v_batch, self.e_batch = self._field(
            f2, "1회 번역 분량(시트 행)", config.BATCH_SIZE, 0,
            tip="한 번에 AI에게 보낼 시트 행 수. 크면 빠르지만 응답이 길어져 누락·오류 위험이 커지고, "
                "작으면 안정적이지만 느립니다. (보통 20~30) "
                "위 '자동'을 켜면 이 값 대신 글자 수 기준으로 자동 결정됩니다.",
            return_entry=True)
        self._toggle_auto_batch()  # 저장된 자동 분량 상태를 입력칸에 반영
        self.v_sends = self._field(
            f2, "AI 대화창 전송 횟수", config.MAX_SENDS_PER_CONVERSATION, 1,
            tip="대화창(채팅) 하나에서 보내는 최대 횟수. 이 횟수를 넘으면 새 대화를 시작합니다. "
                "대화가 길어지면 AI가 느려지거나 맥락이 흐트러져서, 주기적으로 새로 시작합니다.")
        self.v_dmin  = self._field(
            f2, "딜레이 최소 (초)", config.DELAY_MIN, 2,
            tip="한 묶음을 보낸 뒤 다음 묶음까지 기다리는 '최소' 시간(초). "
                "너무 짧으면 과도한 자동화로 차단될 위험이 커집니다.")
        self.v_dmax  = self._field(
            f2, "딜레이 최대 (초)", config.DELAY_MAX, 3,
            tip="묶음 사이 대기의 '최대' 시간(초). 매번 최소~최대 사이에서 무작위로 쉬어 "
                "사람이 쓰는 것처럼 보이게 합니다.")
        self.v_init  = self._field(
            f2, "응답 감지 시작 대기 (초)", getattr(config, "RESPONSE_INIT_WAIT", 2.0), 4,
            tip="메시지를 보낸 직후, AI가 답을 쓰기 시작할 때까지 기다리는 초기 대기(초). "
                "응답 시작이 느린 환경이면 늘리세요.")
        self.v_poll  = self._field(
            f2, "응답 폴링 간격 (초)", getattr(config, "RESPONSE_POLL_INTERVAL", 0.5), 5,
            tip="AI 응답이 끝났는지 확인하는 주기(초). 짧을수록 빨리 감지하지만 더 자주 확인합니다.")
        self.v_done  = self._field(
            f2, "응답 완료 후 대기 (초)", getattr(config, "RESPONSE_DONE_DELAY", 1.0), 6,
            tip="응답이 끝났다고 판단한 뒤, 글자가 완전히 안정될 때까지 추가로 기다리는 시간(초). "
                "결과가 잘리면 늘려보세요.")

        # 플레이스홀더 유지 옵션
        ph_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        ph_frame.pack(fill="x", pady=(8, 0))
        self.v_preserve_ph = tk.BooleanVar(
            value=getattr(config, "PRESERVE_PLACEHOLDERS", True)
        )
        ctk.CTkCheckBox(
            ph_frame,
            text="플레이스홀더 유지 검증 (불일치 시 자동 재번역)",
            variable=self.v_preserve_ph
        ).pack(anchor="w", padx=4)
        ctk.CTkLabel(
            ph_frame,
            text="  · «T:내용» 형식 토큰이 원본과 동일한 개수·내용으로 유지됐는지 검사",
            text_color="#888",
            font=ctk.CTkFont(size=11)
        ).pack(anchor="w", padx=24, pady=(2, 0))

        ctk.CTkFrame(scroll, height=1, fg_color="#3a3a4a").pack(
            fill="x", pady=10)

        # 열 설정 — 현재 작업 모드 기준 (모드별로 따로 저장됨)
        cur_mode = _current_mode()
        is_review_mode = cur_mode in REVIEW_MODES
        ctk.CTkLabel(scroll,
                     text=f"📋  열 설정 — 현재 모드: {WORK_MODE_LABELS.get(cur_mode, cur_mode)}",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(
            anchor="w", pady=(0, 6))
        ctk.CTkLabel(scroll,
                     text="※ 열 역할은 모드(번역/검수)별로 따로 저장됩니다. 모드는 메인 화면에서 전환하세요.",
                     text_color="#888", font=ctk.CTkFont(size=11)).pack(
            anchor="w", padx=4, pady=(0, 4))

        if is_review_mode:
            ROLES = ["source", "review", "category", "ref", None]
        else:
            ROLES = ["source", "ref", "placeholder", None]
        ROLE_LABELS = {"source": "원본", "ref": "참조", "placeholder": "플레이스홀더",
                       "category": "카테고리", "review": "검수대상", None: "제외"}

        col_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        col_frame.pack(fill="x", pady=(0, 4))

        self.col_vars = {}
        for ci, (col, attr, desc) in enumerate([
            ("A", "COL_A_ROLE", "A열"),
            ("B", "COL_B_ROLE", "B열"),
            ("C", "COL_C_ROLE", "C열"),
        ]):
            row_f = ctk.CTkFrame(col_frame, fg_color="transparent")
            row_f.pack(fill="x", pady=3)
            ctk.CTkLabel(row_f, text=f"{desc}", width=40, anchor="w").pack(side="left")
            cur_role = getattr(config, attr, None)
            # 현재 값이 이 모드의 선택지에 없으면 '제외'로 표시 (모드 간 역할 혼입 방지)
            if cur_role not in ROLES:
                cur_role = None
            var = tk.StringVar(value=str(cur_role))
            self.col_vars[attr] = var
            for role in ROLES:
                label = ROLE_LABELS[role]
                val = str(role)
                rb = ctk.CTkRadioButton(row_f, text=label, variable=var, value=val,
                                        width=80 if is_review_mode else 90)
                rb.pack(side="left", padx=3)

        if is_review_mode:
            ctk.CTkLabel(col_frame,
                         text="  · 검수대상: 검수할 번역문이 있는 열 (필수) / 카테고리: 용어 분류 열 (용어집 검수용)",
                         text_color="#888", font=ctk.CTkFont(size=11)).pack(
                anchor="w", padx=4, pady=(2, 0))

        # 결과열
        res_f = ctk.CTkFrame(col_frame, fg_color="transparent")
        res_f.pack(fill="x", pady=(6, 0))
        result_label = "검수 결과 기입" if is_review_mode else "번역 결과 기입"
        ctk.CTkLabel(res_f, text=result_label, width=90, anchor="w").pack(side="left")
        self.v_result_col = tk.StringVar(value=getattr(config, "RESULT_COL", "D"))
        ctk.CTkEntry(res_f, textvariable=self.v_result_col, width=60).pack(side="left", padx=4)
        ctk.CTkLabel(res_f, text="(결과를 적을 열 문자: A, B, C, D ...)",
                     text_color="#888", font=ctk.CTkFont(size=11)).pack(side="left", padx=4)
        if is_review_mode:
            _rc = getattr(config, "RESULT_COL", "D")
            ctk.CTkLabel(col_frame,
                         text=f"  · 결과열({_rc})엔 최종 단어만, 바로 다음 열({next_col_letter(_rc)})엔 판정(OK / 수정+사유)이 기입됩니다.",
                         text_color="#888", font=ctk.CTkFont(size=11)).pack(
                anchor="w", padx=4, pady=(2, 0))
            self.v_cross = tk.BooleanVar(
                value=getattr(config, "REVIEW_CROSS_CHECK", True))
            ctk.CTkCheckBox(
                col_frame,
                text="수정 판정 크로스체크 (다른 언어 규칙·취향 근거의 오탐을 한 번 더 걸러냄)",
                variable=self.v_cross).pack(anchor="w", padx=4, pady=(8, 0))

        ctk.CTkFrame(scroll, height=1, fg_color="#3a3a4a").pack(
            fill="x", pady=10)

        # 번역 언어 (프롬프트 선택)
        ctk.CTkLabel(scroll, text="🌍  번역 언어 (프롬프트)",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(
            anchor="w", pady=(0, 6))
        lang_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        lang_frame.pack(fill="x", pady=(0, 4))

        langs = list_prompt_langs()
        cur_lang = getattr(config, "PROMPT_LANG", "")
        if cur_lang not in langs and langs:
            cur_lang = langs[0]
        self.v_prompt_lang = tk.StringVar(value=cur_lang)

        # 한 줄에 3개씩 그리드 배치
        grid = ctk.CTkFrame(lang_frame, fg_color="transparent")
        grid.pack(fill="x")
        if not langs:
            ctk.CTkLabel(grid, text="prompts 폴더에 프롬프트 파일이 없습니다.",
                         text_color="#e53e3e").grid(row=0, column=0, sticky="w")
        else:
            for i, lang in enumerate(langs):
                r, c = divmod(i, 3)
                ctk.CTkRadioButton(
                    grid, text=LANG_LABELS.get(lang, lang),
                    variable=self.v_prompt_lang, value=lang, width=150
                ).grid(row=r, column=c, sticky="w", padx=4, pady=3)
        ctk.CTkLabel(lang_frame,
                     text="※ 선택한 언어 프롬프트가 매 작업 시 전송됩니다. (행 ID 규칙은 자동 적용)",
                     text_color="#888", font=ctk.CTkFont(size=11)
                     ).pack(anchor="w", padx=4, pady=(4, 0))

        ctk.CTkFrame(scroll, height=1, fg_color="#3a3a4a").pack(
            fill="x", pady=10)

        # AI 모드
        ctk.CTkLabel(scroll, text="🤖  AI 모드",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(
            anchor="w", pady=(0, 6))
        ai_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        ai_frame.pack(fill="x", pady=(0, 12))
        ai_row = ctk.CTkFrame(ai_frame, fg_color="transparent")
        ai_row.pack(fill="x", pady=3)
        ctk.CTkLabel(ai_row, text="모드", width=40, anchor="w").pack(side="left")
        self.v_ai_mode = tk.StringVar(value=getattr(config, "AI_MODE", "chatgpt").lower())
        for val, lbl in [("chatgpt", "ChatGPT (chatgpt.com)"),
                         ("claude",  "Claude (claude.ai)")]:
            ctk.CTkRadioButton(ai_row, text=lbl, variable=self.v_ai_mode, value=val,
                               width=180).pack(side="left", padx=4)
        ctk.CTkLabel(ai_frame,
                     text="※ Claude 모드는 같은 Chrome 프로필에서 claude.ai에 로그인되어 있어야 합니다.",
                     text_color="#888", font=ctk.CTkFont(size=11)
                     ).pack(anchor="w", padx=4, pady=(2, 0))

        ctk.CTkFrame(scroll, height=1, fg_color="#3a3a4a").pack(
            fill="x", pady=10)

        # Chrome 설정
        ctk.CTkLabel(scroll, text="🌐  Chrome 설정",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(
            anchor="w", pady=(0, 6))
        f3 = ctk.CTkFrame(scroll, fg_color="transparent")
        f3.pack(fill="x", pady=(0, 4))

        ctk.CTkLabel(f3, text="Chrome 경로", anchor="w").grid(
            row=0, column=0, padx=(0, 12), pady=6, sticky="w")
        self.v_chrome = tk.StringVar(value=getattr(config, "CHROME_BINARY_PATH", "") or "")
        ctk.CTkEntry(f3, textvariable=self.v_chrome, width=240).grid(
            row=0, column=1, pady=6, sticky="w")
        ctk.CTkButton(f3, text="찾아보기", width=72,
                      command=self._browse_chrome).grid(row=0, column=2, padx=6)

        ctk.CTkLabel(f3, text="디버그 포트", anchor="w").grid(
            row=1, column=0, padx=(0, 12), pady=6, sticky="w")
        self.v_port = tk.StringVar(value=str(config.REMOTE_DEBUGGING_PORT))
        ctk.CTkEntry(f3, textvariable=self.v_port, width=120).grid(
            row=1, column=1, pady=6, sticky="w")

        ctk.CTkLabel(scroll,
                     text="※ Chrome 경로를 비우면 표준 설치 위치에서 자동으로 찾습니다.",
                     text_color="#888", font=ctk.CTkFont(size=11)).pack(
            anchor="w", padx=4)

        ctk.CTkButton(scroll, text="🧙  최초 설정 마법사 다시 실행",
                      fg_color="#4a5568", hover_color="#2d3748",
                      command=self._rerun_wizard).pack(anchor="w", pady=(12, 12))

        # 버튼
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(side="bottom", fill="x", padx=16, pady=12)
        ctk.CTkButton(btn_frame, text="취소", width=90, fg_color="#3a3a3a",
                      command=self.destroy).pack(side="right", padx=(6, 0))
        ctk.CTkButton(btn_frame, text="저장", width=90,
                      command=self._save).pack(side="right")
        self.btn_test = ctk.CTkButton(btn_frame, text="🔌 연결 테스트", width=120,
                                      fg_color="#3182ce", hover_color="#2b6cb0",
                                      command=self._test_connection)
        self.btn_test.pack(side="left")

    def _test_connection(self):
        run_connection_test(self, self.btn_test,
                            spreadsheet=self.v_id.get(), sheet_name=self.v_sheet.get())

    def _collect(self):
        """입력값을 dict 로 모은다 (검증용)."""
        return {
            "START_ROW": self.v_start.get(),
            "BATCH_SIZE": self.v_batch.get(),
            "MAX_SENDS_PER_CONVERSATION": self.v_sends.get(),
            "DELAY_MIN": self.v_dmin.get(),
            "DELAY_MAX": self.v_dmax.get(),
            "REMOTE_DEBUGGING_PORT": self.v_port.get(),
            "RESPONSE_INIT_WAIT": self.v_init.get(),
            "RESPONSE_POLL_INTERVAL": self.v_poll.get(),
            "RESPONSE_DONE_DELAY": self.v_done.get(),
            "RESULT_COL": self.v_result_col.get().strip(),
        }

    def _save(self):
        err = validate_config_values(self._collect())
        if err:
            messagebox.showerror("입력 오류", err, parent=self)
            return
        config.SPREADSHEET_ID             = extract_spreadsheet_id(self.v_id.get())
        config.SHEET_NAME                 = self.v_sheet.get().strip()
        config.START_ROW                  = int(self.v_start.get())
        config.BATCH_SIZE                 = int(self.v_batch.get())
        config.AUTO_BATCH_SIZE            = bool(self.v_auto_batch.get())
        config.MAX_SENDS_PER_CONVERSATION = int(self.v_sends.get())
        config.DELAY_MIN                  = float(self.v_dmin.get())
        config.DELAY_MAX                  = float(self.v_dmax.get())
        config.REMOTE_DEBUGGING_PORT      = int(self.v_port.get())
        config.CHROME_BINARY_PATH         = self.v_chrome.get().strip()
        config.RESPONSE_INIT_WAIT         = float(self.v_init.get())
        config.RESPONSE_POLL_INTERVAL     = float(self.v_poll.get())
        config.RESPONSE_DONE_DELAY        = float(self.v_done.get())
        config.PRESERVE_PLACEHOLDERS      = bool(self.v_preserve_ph.get())
        # 열 설정 저장
        for attr, var in self.col_vars.items():
            val = var.get()
            setattr(config, attr, None if val == "None" else val)
        config.RESULT_COL = self.v_result_col.get().strip().upper() or "D"
        ai_mode = (self.v_ai_mode.get() or "chatgpt").lower()
        config.AI_MODE = ai_mode if ai_mode in ("chatgpt", "claude") else "chatgpt"
        config.PROMPT_LANG = self.v_prompt_lang.get()
        if hasattr(self, "v_cross"):
            config.REVIEW_CROSS_CHECK = bool(self.v_cross.get())
        save_settings()
        self.destroy()

    def _browse_chrome(self):
        p = filedialog.askopenfilename(
            title="Chrome 실행 파일 선택",
            filetypes=[("Chrome", "chrome.exe"), ("실행 파일", "*.exe"),
                       ("모든 파일", "*.*")],
            parent=self)
        if p:
            self.v_chrome.set(p)

    def _rerun_wizard(self):
        parent = self.master
        self.destroy()
        SetupWizard(parent)


# ── 번역 언어 빠른 선택 다이얼로그 ───────────────────────────────────────────

class LangDialog(ctk.CTkToplevel):
    """헤더 🌍 버튼 — 설정 전체를 열지 않고 번역 언어만 빠르게 고르는 창.
    라디오를 누르면 즉시 config.PROMPT_LANG에 반영·저장된다."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("번역 언어 선택")
        self.geometry("460x320")
        self.resizable(False, False)
        self.grab_set()
        self.after(50, self.lift)
        self.configure(fg_color="#f0f7ff")
        self._build()

    def _build(self):
        ctk.CTkLabel(self, text="🌍  번역 언어 (프롬프트)",
                     font=ctk.CTkFont(size=15, weight="bold"),
                     text_color="#2d3748").pack(anchor="w", padx=24, pady=(20, 4))

        langs = list_prompt_langs()
        cur = getattr(config, "PROMPT_LANG", "")
        if cur not in langs and langs:
            cur = langs[0]
        self.v_lang = tk.StringVar(value=cur)

        grid = ctk.CTkFrame(self, fg_color="transparent")
        grid.pack(fill="both", expand=True, padx=20, pady=(4, 0))

        if not langs:
            ctk.CTkLabel(grid, text="prompts 폴더에 프롬프트 파일이 없습니다.",
                         text_color="#e53e3e").grid(row=0, column=0, sticky="w")
        else:
            for i, lang in enumerate(langs):
                r, c = divmod(i, 3)
                ctk.CTkRadioButton(
                    grid, text=LANG_LABELS.get(lang, lang),
                    variable=self.v_lang, value=lang, width=130,
                    command=self._apply,            # 누르는 즉시 적용·저장
                    fg_color="#4fd1c5", hover_color="#38b2ac",
                    text_color="#2d3748",
                ).grid(row=r, column=c, sticky="w", padx=6, pady=8)

        self.status = ctk.CTkLabel(
            self, text=f"현재: {LANG_LABELS.get(cur, cur) or '(미선택)'}",
            font=ctk.CTkFont(size=12), text_color="#718096")
        self.status.pack(anchor="w", padx=24, pady=(2, 0))

        btn = ctk.CTkFrame(self, fg_color="transparent")
        btn.pack(fill="x", padx=20, pady=(8, 16))
        ctk.CTkButton(btn, text="닫기", width=90,
                      fg_color="#4fd1c5", hover_color="#38b2ac",
                      text_color="white", command=self.destroy).pack(side="right")

    def _apply(self):
        """라디오 선택 즉시 config 반영 + settings.json 저장 + 상태 문구 갱신"""
        lang = self.v_lang.get()
        config.PROMPT_LANG = lang
        save_settings()
        self.status.configure(text=f"현재: {LANG_LABELS.get(lang, lang)} — 저장됨 ✓")


# ── 프롬프트 편집 다이얼로그 ─────────────────────────────────────────────────

class PromptDialog(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("프롬프트 편집")
        self.geometry("680x560")
        self.grab_set()
        self.after(50, self.lift)
        self._build()

    def _build(self):
        # 현재 작업 모드에 맞는 프롬프트 파일을 연다:
        #   번역 모드 → prompts/{언어}.txt / 검수 모드 → prompts/review_*.txt 템플릿
        mode = _current_mode()
        self.is_review = mode in REVIEW_MODES
        if self.is_review:
            self.lang = mode  # 파일명 키로 모드명을 그대로 사용
            self.prompt_file = os.path.join(PROMPTS_DIR, f"{mode}.txt")
            title_txt = f"프롬프트 편집 — {WORK_MODE_LABELS.get(mode, mode)} 템플릿"
            hint_txt = ("※ {SRC_LANG} / {TGT_LANG} 토큰은 실행 시 메인 화면에서 고른 검수 언어로 자동 치환됩니다. "
                        "행 ID 규칙은 자동으로 덧붙습니다.")
        else:
            self.lang = getattr(config, "PROMPT_LANG", "")
            self.prompt_file = os.path.join(PROMPTS_DIR, f"{self.lang}.txt")
            title_txt = f"프롬프트 편집 — 현재 언어: {LANG_LABELS.get(self.lang, self.lang) or '(미선택)'}"
            hint_txt = "※ 여기엔 순수 번역 규칙만 작성하세요. 행 ID 규칙은 작업 시 자동으로 덧붙습니다."

        ctk.CTkLabel(self,
                     text=title_txt,
                     font=ctk.CTkFont(size=13, weight="bold")).pack(
            anchor="w", padx=20, pady=(16, 2))
        ctk.CTkLabel(self,
                     text=hint_txt,
                     text_color="#888", font=ctk.CTkFont(size=11)).pack(
            anchor="w", padx=20, pady=(0, 6))

        self.txt = ctk.CTkTextbox(self, font=ctk.CTkFont(family="Consolas", size=11))
        self.txt.pack(fill="both", expand=True, padx=20, pady=(0, 8))

        if os.path.exists(self.prompt_file):
            with open(self.prompt_file, "r", encoding="utf-8") as f:
                self.txt.insert("1.0", f.read())
        else:
            self.txt.insert("1.0", f"(prompts/{self.lang}.txt 파일이 없습니다. 내용을 작성 후 저장하면 생성됩니다.)")

        btn = ctk.CTkFrame(self, fg_color="transparent")
        btn.pack(fill="x", padx=20, pady=(0, 16))
        ctk.CTkButton(btn, text="취소", width=80, fg_color="#3a3a3a",
                      command=self.destroy).pack(side="right", padx=(6, 0))
        ctk.CTkButton(btn, text="저장", width=80,
                      command=self._save).pack(side="right")

    def _save(self):
        if not self.lang:
            messagebox.showwarning("언어 미선택", "먼저 설정에서 번역 언어를 선택해주세요.", parent=self)
            return
        content = self.txt.get("1.0", "end").rstrip()
        os.makedirs(PROMPTS_DIR, exist_ok=True)
        with open(self.prompt_file, "w", encoding="utf-8") as f:
            f.write(content)
        self.destroy()


# ── 완료 팝업 ────────────────────────────────────────────────────────────────

class DoneDialog(ctk.CTkToplevel):
    def __init__(self, parent, processed, total, fail_lines):
        super().__init__(parent)
        self.title("작업 완료 — 결과 요약")
        self.geometry("460x380")
        self.grab_set()
        self.after(50, self.lift)
        self._build(processed, total, fail_lines)

    def _build(self, processed, total, fail_lines):
        ctk.CTkLabel(self, text=f"✅  완료: {processed:,} / {total:,} 행",
                     font=ctk.CTkFont(size=16, weight="bold"),
                     text_color="#9ece6a").pack(pady=(24, 8))

        if fail_lines:
            ctk.CTkLabel(self,
                         text=f"⚠️  실패 {len(fail_lines)}건 (재실행 시 자동 재처리됩니다)",
                         text_color="#e0af68").pack()
            box = ctk.CTkTextbox(self, font=ctk.CTkFont(family="Consolas", size=10),
                                 height=180)
            box.pack(fill="both", expand=True, padx=20, pady=8)
            box.insert("1.0", "\n".join(fail_lines))
            box.configure(state="disabled")
        else:
            ctk.CTkLabel(self, text="실패 항목 없음 🎉",
                         font=ctk.CTkFont(size=13),
                         text_color="#7aa2f7").pack(pady=20)

        ctk.CTkButton(self, text="닫기", width=100,
                      command=self.destroy).pack(pady=(8, 20))


# ── 설정 저장/불러오기 ──────────────────────────────────────────────────────

def save_settings():
    """현재 config 값을 settings.json에 저장"""
    import json
    # COL_*_ROLE 은 현재 모드의 사본이므로, 저장 전에 모드 프리셋에 동기화한다
    store_mode_columns()
    data = {
        "SPREADSHEET_ID":             config.SPREADSHEET_ID,
        "SHEET_NAME":                 config.SHEET_NAME,
        "START_ROW":                  config.START_ROW,
        "BATCH_SIZE":                 config.BATCH_SIZE,
        "AUTO_BATCH_SIZE":            getattr(config, "AUTO_BATCH_SIZE", False),
        "MAX_SENDS_PER_CONVERSATION": config.MAX_SENDS_PER_CONVERSATION,
        "DELAY_MIN":                  config.DELAY_MIN,
        "DELAY_MAX":                  config.DELAY_MAX,
        "REMOTE_DEBUGGING_PORT":      config.REMOTE_DEBUGGING_PORT,
        "COL_A_ROLE":                 config.COL_A_ROLE,
        "COL_B_ROLE":                 config.COL_B_ROLE,
        "COL_C_ROLE":                 config.COL_C_ROLE,
        "RESULT_COL":                 config.RESULT_COL,
        "RESPONSE_INIT_WAIT":         config.RESPONSE_INIT_WAIT,
        "RESPONSE_POLL_INTERVAL":     config.RESPONSE_POLL_INTERVAL,
        "RESPONSE_DONE_DELAY":        config.RESPONSE_DONE_DELAY,
        "PRESERVE_PLACEHOLDERS":      getattr(config, "PRESERVE_PLACEHOLDERS", True),
        "AI_MODE":                    getattr(config, "AI_MODE", "chatgpt"),
        "PROMPT_LANG":                getattr(config, "PROMPT_LANG", ""),
        "CHROME_BINARY_PATH":         getattr(config, "CHROME_BINARY_PATH", ""),
        "WORK_MODE":                  _current_mode(),
        "REVIEW_SRC_LANG":            getattr(config, "REVIEW_SRC_LANG", "ko"),
        "REVIEW_TGT_LANG":            getattr(config, "REVIEW_TGT_LANG", "es"),
        "REVIEW_CROSS_CHECK":         getattr(config, "REVIEW_CROSS_CHECK", True),
        "MODE_COL_ROLES":             _mode_presets(),
    }
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_settings():
    """settings.json이 있으면 config에 반영"""
    import json
    if not os.path.exists(SETTINGS_FILE):
        return
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        config.SPREADSHEET_ID             = data.get("SPREADSHEET_ID",             config.SPREADSHEET_ID)
        config.SHEET_NAME                 = data.get("SHEET_NAME",                 config.SHEET_NAME)
        config.START_ROW                  = data.get("START_ROW",                  config.START_ROW)
        config.BATCH_SIZE                 = data.get("BATCH_SIZE",                 config.BATCH_SIZE)
        config.AUTO_BATCH_SIZE            = bool(data.get("AUTO_BATCH_SIZE",
                                                          getattr(config, "AUTO_BATCH_SIZE", False)))
        config.MAX_SENDS_PER_CONVERSATION = data.get("MAX_SENDS_PER_CONVERSATION", config.MAX_SENDS_PER_CONVERSATION)
        config.DELAY_MIN                  = data.get("DELAY_MIN",                  config.DELAY_MIN)
        config.DELAY_MAX                  = data.get("DELAY_MAX",                  config.DELAY_MAX)
        config.REMOTE_DEBUGGING_PORT      = data.get("REMOTE_DEBUGGING_PORT",      config.REMOTE_DEBUGGING_PORT)
        config.COL_A_ROLE                 = data.get("COL_A_ROLE",                 config.COL_A_ROLE)
        config.COL_B_ROLE                 = data.get("COL_B_ROLE",                 config.COL_B_ROLE)
        config.COL_C_ROLE                 = data.get("COL_C_ROLE",                 config.COL_C_ROLE)
        config.RESULT_COL                 = data.get("RESULT_COL",                 config.RESULT_COL)
        config.RESPONSE_INIT_WAIT         = data.get("RESPONSE_INIT_WAIT",         config.RESPONSE_INIT_WAIT)
        config.RESPONSE_POLL_INTERVAL     = data.get("RESPONSE_POLL_INTERVAL",     config.RESPONSE_POLL_INTERVAL)
        config.RESPONSE_DONE_DELAY        = data.get("RESPONSE_DONE_DELAY",        config.RESPONSE_DONE_DELAY)
        config.PRESERVE_PLACEHOLDERS      = data.get("PRESERVE_PLACEHOLDERS",      True)
        config.CHROME_BINARY_PATH         = data.get("CHROME_BINARY_PATH",         getattr(config, "CHROME_BINARY_PATH", ""))
        ai_mode = (data.get("AI_MODE", "chatgpt") or "chatgpt").lower()
        config.AI_MODE = ai_mode if ai_mode in ("chatgpt", "claude") else "chatgpt"
        # 프롬프트 언어 — 저장값이 실제 존재하는 파일일 때만 적용, 아니면 첫 번째로 폴백
        saved_lang = data.get("PROMPT_LANG", getattr(config, "PROMPT_LANG", ""))
        langs = list_prompt_langs()
        if saved_lang in langs:
            config.PROMPT_LANG = saved_lang
        elif langs:
            config.PROMPT_LANG = langs[0]
        else:
            config.PROMPT_LANG = ""

        # ── 작업 모드 + 모드별 열 역할 ─────────────────────────────
        wm = (data.get("WORK_MODE", "translate") or "translate")
        config.WORK_MODE = wm if wm in DEFAULT_MODE_COLS else "translate"
        src = data.get("REVIEW_SRC_LANG", getattr(config, "REVIEW_SRC_LANG", "ko"))
        tgt = data.get("REVIEW_TGT_LANG", getattr(config, "REVIEW_TGT_LANG", "es"))
        config.REVIEW_SRC_LANG = src if src in REVIEW_LANGS else "ko"
        config.REVIEW_TGT_LANG = tgt if tgt in REVIEW_LANGS else "es"
        config.REVIEW_CROSS_CHECK = bool(data.get("REVIEW_CROSS_CHECK", True))

        presets = data.get("MODE_COL_ROLES")
        if isinstance(presets, dict):
            config.MODE_COL_ROLES = presets
        else:
            # 구버전 settings.json — 위에서 읽은 COL_* 값을 번역 모드 프리셋으로 승격
            config.MODE_COL_ROLES = {
                "translate": {
                    "COL_A_ROLE": config.COL_A_ROLE,
                    "COL_B_ROLE": config.COL_B_ROLE,
                    "COL_C_ROLE": config.COL_C_ROLE,
                    "RESULT_COL": config.RESULT_COL,
                }
            }
        # 프리셋 보정 후 현재 모드의 열 역할을 COL_* 에 적용
        apply_mode_columns()

        _clamp_settings()
    except Exception:
        pass


def _clamp_settings():
    """수기 편집/손상된 settings.json 의 비정상 값을 안전 범위로 보정한다."""
    try:
        if config.BATCH_SIZE < 1:
            config.BATCH_SIZE = 1
        if config.MAX_SENDS_PER_CONVERSATION < 1:
            config.MAX_SENDS_PER_CONVERSATION = 1
        if config.START_ROW < 1:
            config.START_ROW = 1
        if config.DELAY_MIN < 0:
            config.DELAY_MIN = 0.0
        if config.DELAY_MAX < config.DELAY_MIN:
            config.DELAY_MIN, config.DELAY_MAX = config.DELAY_MAX, config.DELAY_MIN
        for attr in ("RESPONSE_INIT_WAIT", "RESPONSE_POLL_INTERVAL", "RESPONSE_DONE_DELAY"):
            if getattr(config, attr, 0) < 0:
                setattr(config, attr, 0.0)
        if not (1 <= config.REMOTE_DEBUGGING_PORT <= 65535):
            config.REMOTE_DEBUGGING_PORT = 9222
    except Exception:
        pass


# ── 메인 앱 ──────────────────────────────────────────────────────────────────

class SetupWizard(ctk.CTkToplevel):
    """최초 설정 마법사 — 첫 실행 시(또는 설정에서 수동 호출 시) 기본값을 모은다.

    exe 안에 포함되어 있어 별도 파일 없이 동작한다. Chrome 경로/스프레드시트/
    기본 번역 설정을 한 화면에서 받고 settings.json 에 저장한다.
    """

    def __init__(self, parent):
        super().__init__(parent)
        self._parent = parent
        self.title("최초 설정 마법사")
        self.geometry("560x640")
        self.resizable(True, True)
        self.grab_set()
        self.after(50, self.lift)
        self._build()

    def _row(self, parent, label, value, row, hint=None):
        ctk.CTkLabel(parent, text=label, anchor="w").grid(
            row=row, column=0, padx=(0, 12), pady=6, sticky="w")
        var = tk.StringVar(value=str(value))
        ctk.CTkEntry(parent, textvariable=var, width=300).grid(
            row=row, column=1, columnspan=2, pady=6, sticky="w")
        return var

    def _build(self):
        ctk.CTkLabel(self, text="환영합니다! 👋  기본 설정을 진행합니다.",
                     font=ctk.CTkFont(size=15, weight="bold")).pack(
            anchor="w", padx=20, pady=(16, 0))
        ctk.CTkLabel(self,
                     text="언제든 설정창에서 다시 실행할 수 있습니다.",
                     text_color="#888", font=ctk.CTkFont(size=11)).pack(
            anchor="w", padx=20, pady=(2, 8))

        scroll = ctk.CTkScrollableFrame(self)
        scroll.pack(fill="both", expand=True, padx=16, pady=(0, 0))

        # 1) Chrome
        ctk.CTkLabel(scroll, text="① Chrome",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w", pady=(4, 6))
        cf = ctk.CTkFrame(scroll, fg_color="transparent")
        cf.pack(fill="x")
        ctk.CTkLabel(cf, text="Chrome 경로", anchor="w").grid(
            row=0, column=0, padx=(0, 12), pady=6, sticky="w")
        # 설정값이 없으면 표준 위치 자동 탐색 결과를 미리 채운다
        prefill = (getattr(config, "CHROME_BINARY_PATH", "") or "").strip() or find_chrome()
        self.v_chrome = tk.StringVar(value=prefill)
        ctk.CTkEntry(cf, textvariable=self.v_chrome, width=300).grid(
            row=0, column=1, pady=6, sticky="w")
        ctk.CTkButton(cf, text="찾아보기", width=72,
                      command=self._browse_chrome).grid(row=0, column=2, padx=6)
        ctk.CTkLabel(scroll,
                     text="※ 자동으로 찾았으면 그대로 두세요. 못 찾았으면 chrome.exe 를 직접 지정합니다.",
                     text_color="#888", font=ctk.CTkFont(size=11)).pack(anchor="w", padx=4, pady=(0, 8))

        # 2) 스프레드시트
        ctk.CTkLabel(scroll, text="② 스프레드시트",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w", pady=(6, 6))
        sf = ctk.CTkFrame(scroll, fg_color="transparent")
        sf.pack(fill="x")
        self.v_id    = self._row(sf, "스프레드시트 주소", config.SPREADSHEET_ID, 0)
        self.v_sheet = self._row(sf, "시트(탭) 이름",    config.SHEET_NAME,      1)
        self.v_start = self._row(sf, "시작 행 번호",     config.START_ROW,       2)

        # credentials.json — 파일 선택 시 exe 폴더로 복사한다
        ctk.CTkLabel(sf, text="credentials.json", anchor="w").grid(
            row=3, column=0, padx=(0, 12), pady=6, sticky="w")
        self._cred_src = None
        cred_exists = os.path.exists(paths.app_path("credentials.json"))
        self.v_cred = tk.StringVar(value="(이미 설정됨)" if cred_exists else "")
        ctk.CTkEntry(sf, textvariable=self.v_cred, width=224).grid(
            row=3, column=1, pady=6, sticky="w")
        ctk.CTkButton(sf, text="찾아보기", width=72,
                      command=self._browse_cred).grid(row=3, column=2, padx=6)
        ctk.CTkLabel(scroll, justify="left", text_color="#888",
                     font=ctk.CTkFont(size=11),
                     text=("· 주소: 브라우저의 스프레드시트 주소(URL)를 그대로 붙여넣으세요.\n"
                           "· 시트(탭) 이름: 화면 아래쪽 탭 이름과 똑같이. (예: 시트1)\n"
                           "· credentials.json: 구글 서비스 계정 키(.json). 선택하면 폴더로 복사됩니다.")
                     ).pack(anchor="w", padx=4, pady=(2, 6))

        share_box, self._set_email = make_share_help(scroll, self)
        share_box.pack(fill="x", padx=4, pady=(0, 8))
        self._set_email(get_service_account_email())

        # 3) 기본 번역 설정
        ctk.CTkLabel(scroll, text="③ 기본 번역 설정",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w", pady=(6, 6))
        tf = ctk.CTkFrame(scroll, fg_color="transparent")
        tf.pack(fill="x")
        self.v_batch  = self._row(tf, "1회 번역 분량(시트 행)", config.BATCH_SIZE, 0)
        self.v_result = self._row(tf, "번역 결과 기입(열)", getattr(config, "RESULT_COL", "D"), 1)

        # 번역 언어
        lf = ctk.CTkFrame(scroll, fg_color="transparent")
        lf.pack(fill="x", pady=(6, 0))
        ctk.CTkLabel(lf, text="번역 언어", anchor="w").grid(
            row=0, column=0, padx=(0, 12), pady=6, sticky="w")
        self._langs = list_prompt_langs()
        self._lang_labels = [LANG_LABELS.get(l, l) for l in self._langs]
        cur = getattr(config, "PROMPT_LANG", "")
        cur_label = LANG_LABELS.get(cur, cur) if cur in self._langs else (
            self._lang_labels[0] if self._lang_labels else "")
        self.v_lang_label = tk.StringVar(value=cur_label)
        if self._lang_labels:
            ctk.CTkOptionMenu(lf, values=self._lang_labels,
                              variable=self.v_lang_label, width=200).grid(
                row=0, column=1, pady=6, sticky="w")
        else:
            ctk.CTkLabel(lf, text="(prompts 없음)", text_color="#e53e3e").grid(
                row=0, column=1, pady=6, sticky="w")

        # 버튼
        btn = ctk.CTkFrame(self, fg_color="transparent")
        btn.pack(side="bottom", fill="x", padx=16, pady=12)
        ctk.CTkButton(btn, text="나중에", width=90, fg_color="#3a3a3a",
                      command=self.destroy).pack(side="right", padx=(6, 0))
        ctk.CTkButton(btn, text="완료", width=110,
                      command=self._finish).pack(side="right")
        self.btn_test = ctk.CTkButton(btn, text="🔌 연결 테스트", width=120,
                                      fg_color="#3182ce", hover_color="#2b6cb0",
                                      command=self._test_connection)
        self.btn_test.pack(side="left")

    def _test_connection(self):
        # 마법사에서 고른 credentials 가 아직 폴더에 없으면 먼저 복사해 테스트한다
        if self._cred_src and not self._copy_credentials():
            return
        run_connection_test(self, self.btn_test,
                            spreadsheet=self.v_id.get(), sheet_name=self.v_sheet.get())

    def _browse_chrome(self):
        p = filedialog.askopenfilename(
            title="Chrome 실행 파일 선택",
            filetypes=[("Chrome", "chrome.exe"), ("실행 파일", "*.exe"),
                       ("모든 파일", "*.*")],
            parent=self)
        if p:
            self.v_chrome.set(p)

    def _browse_cred(self):
        p = filedialog.askopenfilename(
            title="credentials.json 선택",
            filetypes=[("JSON", "*.json"), ("모든 파일", "*.*")],
            parent=self)
        if p:
            self._cred_src = p
            self.v_cred.set(p)
            # 선택한 키 파일에서 서비스 계정 이메일을 읽어 공유 안내에 표시
            try:
                import json as _json
                with open(p, "r", encoding="utf-8") as f:
                    email = (_json.load(f).get("client_email") or "").strip()
                if email:
                    self._set_email(email)
            except Exception:
                pass

    def _copy_credentials(self):
        """선택한 credentials.json 을 exe 폴더로 복사. 성공/스킵 시 True."""
        if not self._cred_src:
            return True   # 선택 안 함 → 기존 파일 유지
        import json as _json
        import shutil
        dest = paths.app_path("credentials.json")
        try:
            with open(self._cred_src, "r", encoding="utf-8") as f:
                data = _json.load(f)
            if "client_email" not in data and data.get("type") != "service_account":
                if not messagebox.askyesno(
                        "확인", "선택한 파일이 서비스 계정 키가 아닐 수 있습니다.\n"
                                "그래도 사용할까요?", parent=self):
                    return False
        except Exception:
            if not messagebox.askyesno(
                    "확인", "선택한 파일이 올바른 JSON 이 아닙니다.\n"
                            "그래도 복사할까요?", parent=self):
                return False
        try:
            if os.path.abspath(self._cred_src) != os.path.abspath(dest):
                shutil.copy2(self._cred_src, dest)
        except Exception as e:
            messagebox.showerror("오류", f"credentials.json 복사 실패:\n{e}", parent=self)
            return False
        return True

    def _finish(self):
        err = validate_config_values({
            "START_ROW": self.v_start.get(),
            "BATCH_SIZE": self.v_batch.get(),
            "RESULT_COL": self.v_result.get().strip(),
        })
        if err:
            messagebox.showerror("입력 오류", err, parent=self)
            return
        try:
            config.CHROME_BINARY_PATH = self.v_chrome.get().strip()
            config.SPREADSHEET_ID     = extract_spreadsheet_id(self.v_id.get())
            config.SHEET_NAME         = self.v_sheet.get().strip()
            config.START_ROW          = int(self.v_start.get())
            config.BATCH_SIZE         = int(self.v_batch.get())
            config.RESULT_COL         = self.v_result.get().strip().upper() or "D"
            # 언어 라벨 → 코드
            if self._langs:
                label = self.v_lang_label.get()
                lang = next((c for c, l in zip(self._langs, self._lang_labels)
                             if l == label), self._langs[0])
                config.PROMPT_LANG = lang
        except ValueError as e:
            messagebox.showerror("입력 오류", f"숫자 형식을 확인해주세요.\n{e}", parent=self)
            return

        if not config.SPREADSHEET_ID:
            if not messagebox.askyesno(
                    "확인", "스프레드시트 ID 가 비어 있습니다.\n그래도 저장할까요?",
                    parent=self):
                return

        # credentials.json 복사 (선택했을 때만)
        if not self._copy_credentials():
            return

        save_settings()
        # 메인 화면 언어 표시 갱신
        refresh = getattr(self._parent, "_refresh_lang_label", None)
        if callable(refresh):
            try:
                refresh()
            except Exception:
                pass
        messagebox.showinfo("설정 완료",
                            "기본 설정을 저장했습니다.\n언제든 설정창(⚙)에서 변경할 수 있습니다.",
                            parent=self._parent)
        self.destroy()


class App(ctk.CTk):
    # Clay UI 전용 컬러 팔레트
    COLORS = {
        "bg_main":        "#f0f7ff",
        "card_bg":        "#ffffff",
        "accent":         "#4fd1c5",
        "accent_hover":   "#38b2ac",
        "secondary":      "#cbd5e0",
        "secondary_hover":"#a0aec0",
        "text_main":      "#2d3748",
        "text_sub":       "#718096",
        "log_bg":         "#ffffff",
        "progress_bg":    "#e2e8f0",
    }

    LOG_COLORS = {
        "info":    "#4a5568",
        "success": "#38a169",
        "warn":    "#dd6b20",
        "error":   "#e53e3e",
    }

    def __init__(self):
        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("blue")
        super().__init__()

        self.title("RO Localization Tool")
        self.geometry("620x720")
        self.minsize(520, 560)
        self.configure(fg_color=self.COLORS["bg_main"])

        self.log_queue = queue.Queue()
        self.worker = None
        self._waiting_msg = ""
        self._dot_count = 0
        self._is_waiting = False

        # settings.json 이 없으면 최초 실행 → 설정 마법사를 띄운다
        self._first_run = not os.path.exists(SETTINGS_FILE)
        load_settings()
        self._build_ui()
        self._poll()
        self._animate()
        # UI가 뜬 뒤 백그라운드로 업데이트 확인 (네트워크 지연이 UI를 막지 않도록)
        self.after(800, self._check_update_async)
        if self._first_run:
            self.after(300, self._run_setup_wizard)

    def _run_setup_wizard(self):
        SetupWizard(self)

    def _build_ui(self):
        # ── 헤더
        header = ctk.CTkFrame(self, height=60, corner_radius=0,
                              fg_color=self.COLORS["card_bg"])
        header.pack(fill="x", pady=(0, 10))
        header.pack_propagate(False)

        bar = ctk.CTkFrame(header, width=4, height=28, corner_radius=2,
                           fg_color=self.COLORS["accent"])
        bar.pack(side="left", padx=(25, 10))
        bar.pack_propagate(False)

        ctk.CTkLabel(header, text="RO Localization Tool",
                     font=ctk.CTkFont(family="Inter", size=16, weight="bold"),
                     text_color=self.COLORS["text_main"]).pack(side="left")

        for icon, cmd in [("⚙", self._open_settings),
                          ("📝", self._open_prompt),
                          ("🌍", self._open_lang),
                          ("🔄", self._check_update_manual)]:
            ctk.CTkButton(header, text=icon, width=40, height=35,
                          font=ctk.CTkFont(size=18),
                          fg_color="transparent", hover_color="#edf2f7",
                          text_color=self.COLORS["text_sub"],
                          command=cmd).pack(side="right", padx=5)

        # ── 상태 카드
        card = ctk.CTkFrame(self, corner_radius=25, fg_color=self.COLORS["card_bg"],
                            border_width=2, border_color="#eef2f7")
        card.pack(fill="x", padx=20, pady=10)

        status_row = ctk.CTkFrame(card, fg_color="transparent")
        status_row.pack(fill="x", padx=20, pady=(18, 5))

        self.dot_lbl = ctk.CTkLabel(status_row, text="●",
                                     text_color=self.COLORS["secondary"],
                                     font=ctk.CTkFont(size=14))
        self.dot_lbl.pack(side="left")
        self.status_lbl = ctk.CTkLabel(status_row, text="  Ready",
                                        font=ctk.CTkFont(size=14, weight="bold"),
                                        text_color=self.COLORS["text_main"])
        self.status_lbl.pack(side="left")
        self.prog_lbl = ctk.CTkLabel(status_row, text="0 / 0 Rows (0%)",
                                      font=ctk.CTkFont(size=12),
                                      text_color=self.COLORS["text_sub"])
        self.prog_lbl.pack(side="right")

        # ── 작업 모드 선택 (번역 / 용어집 검수 / 일반 검수)
        # 무채색 알약형 커스텀 세그먼트 — 선택된 항목만 흰 알약 + 포인트 색 글자.
        mode_row = ctk.CTkFrame(card, fg_color="transparent")
        mode_row.pack(fill="x", padx=20, pady=(0, 8))
        self._mode_font        = ctk.CTkFont(size=12)
        self._mode_font_bold   = ctk.CTkFont(size=12, weight="bold")
        seg_wrap = ctk.CTkFrame(mode_row, fg_color="#eef2f7", corner_radius=18)
        seg_wrap.pack(fill="x")
        self._mode_btns = {}
        for m in ("translate", "review_glossary", "review_general"):
            btn = ctk.CTkButton(
                seg_wrap, text=WORK_MODE_LABELS[m], height=30, corner_radius=14,
                font=self._mode_font,
                fg_color="transparent", hover_color="#e2e8f0",
                text_color=self.COLORS["text_sub"],
                command=lambda mm=m: self._on_mode_change(mm))
            btn.pack(side="left", expand=True, fill="x", padx=3, pady=3)
            self._mode_btns[m] = btn
        self._update_mode_buttons()

        # ── 언어 표시 영역 (모드에 따라 내용이 바뀜)
        #   번역 모드: 현재 번역 언어 표시 / 검수 모드: 원문 → 대상 언어 선택
        self.lang_area = ctk.CTkFrame(card, fg_color="transparent")
        self.lang_area.pack(fill="x", padx=20, pady=(0, 2))
        self._build_lang_area()

        self.progress = ctk.CTkProgressBar(card, height=12, corner_radius=6,
                                           fg_color=self.COLORS["progress_bg"],
                                           progress_color=self.COLORS["accent"])
        self.progress.set(0)
        self.progress.pack(fill="x", padx=20, pady=(0, 20))

        # ── 제어 버튼
        ctrl = ctk.CTkFrame(self, fg_color="transparent")
        ctrl.pack(fill="x", padx=20, pady=15)

        self.btn_start = ctk.CTkButton(
            ctrl, text="RUN", width=120, height=50,
            corner_radius=18,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=self.COLORS["accent"], hover_color=self.COLORS["accent_hover"],
            text_color="white",
            command=self._start)
        self.btn_start.pack(side="left", padx=(0, 10))

        self.btn_stop = ctk.CTkButton(
            ctrl, text="STOP", width=120, height=50,
            corner_radius=18,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color=self.COLORS["secondary"], hover_color=self.COLORS["secondary_hover"],
            text_color=self.COLORS["text_main"],
            state="disabled",
            command=self._stop)
        self.btn_stop.pack(side="left", padx=(0, 10))

        self.btn_pause = ctk.CTkButton(
            ctrl, text="PAUSE", width=120, height=50,
            corner_radius=18,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color=self.COLORS["secondary"], hover_color=self.COLORS["secondary_hover"],
            text_color=self.COLORS["text_main"],
            state="disabled",
            command=self._pause)
        self.btn_pause.pack(side="left", padx=(0, 10))

        ctk.CTkButton(
            ctrl, text="🗑", width=50, height=50,
            corner_radius=18,
            font=ctk.CTkFont(size=16),
            fg_color=self.COLORS["secondary"], hover_color=self.COLORS["secondary_hover"],
            text_color=self.COLORS["text_main"],
            command=self._clear_log).pack(side="right")

        # ── 로그 영역
        log_hdr = ctk.CTkFrame(self, fg_color="transparent")
        log_hdr.pack(fill="x", padx=25, pady=(5, 5))
        ctk.CTkLabel(log_hdr, text="SYSTEM LOG",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=self.COLORS["text_sub"]).pack(side="left")

        self.log_box = ctk.CTkTextbox(
            self, corner_radius=20,
            fg_color=self.COLORS["log_bg"],
            border_width=2, border_color="#eef2f7",
            font=ctk.CTkFont(family="Consolas", size=11),
            text_color=self.COLORS["text_sub"],
            wrap="word", state="disabled")
        self.log_box.pack(fill="both", expand=True, padx=20, pady=(0, 25))

        for tag, color in self.LOG_COLORS.items():
            self.log_box.tag_config(tag, foreground=color)

    # ── 액션 ─────────────────────────────────────────────────────────────────

    def _start(self):
        creds = os.path.join(BASE_DIR, "credentials.json")
        if not os.path.exists(creds):
            if messagebox.askyesno(
                    "credentials.json 없음",
                    "credentials.json 파일이 없습니다.\n"
                    "지금 설정 마법사에서 지정할까요?"):
                self._run_setup_wizard()
            return
        if not config.SPREADSHEET_ID:
            messagebox.showerror("오류", "설정에서 스프레드시트 ID를 입력해주세요.")
            return

        mode = _current_mode()
        if mode in REVIEW_MODES:
            # ── 검수 모드: 언어쌍/열 역할 확인 + 검수 템플릿 로드 ────────
            src = getattr(config, "REVIEW_SRC_LANG", "ko")
            tgt = getattr(config, "REVIEW_TGT_LANG", "es")
            if src == tgt:
                messagebox.showerror(
                    "오류", "검수 원문 언어와 대상 언어가 같습니다.\n"
                           "메인 화면에서 서로 다른 언어를 선택해주세요.")
                return
            roles = [getattr(config, a, None)
                     for a in ("COL_A_ROLE", "COL_B_ROLE", "COL_C_ROLE")]
            if "review" not in roles:
                messagebox.showerror(
                    "오류", "검수 대상 열이 지정되지 않았습니다.\n"
                           "설정(⚙) → 열 설정에서 '검수대상' 열을 선택해주세요.")
                return
            loaded = load_review_prompt(mode, src, tgt)
            if not loaded:
                messagebox.showerror(
                    "오류", f"검수 프롬프트 파일을 불러오지 못했습니다.\n"
                           f"(prompts/{mode}.txt)")
                return
            config.FIXED_PROMPT = loaded
            if mode == "review_glossary" and "category" not in roles:
                self._add_log("⚠️ 카테고리 열이 지정되지 않았습니다 — 카테고리 없이 검수합니다.", "warn")
            self._add_log(
                f"검수 프롬프트 로드: {WORK_MODE_LABELS.get(mode, mode)} · "
                f"{LANG_LABELS.get(src, src)} → {LANG_LABELS.get(tgt, tgt)}", "info")
        else:
            # ── 번역 모드: 선택된 언어 프롬프트 로드 (+ ID 규칙 자동 주입) ──
            lang = getattr(config, "PROMPT_LANG", "")
            loaded = load_prompt(lang)
            if not loaded:
                langs = list_prompt_langs()
                if not langs:
                    messagebox.showerror("오류", f"prompts 폴더에 프롬프트 파일이 없습니다.\n({PROMPTS_DIR})")
                    return
                messagebox.showerror("오류", f"프롬프트 '{lang}'을(를) 불러오지 못했습니다.\n설정에서 언어를 선택해주세요.")
                return
            config.FIXED_PROMPT = loaded
            self._add_log(f"프롬프트 로드: {lang} (행 ID 규칙 자동 적용)", "info")

        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        # 실행 중 모드 전환 방지 (열 역할/프롬프트가 도중에 바뀌면 안 됨)
        self._set_mode_selector_state("disabled")

        self.btn_pause.configure(state="normal", text="PAUSE",
                                fg_color=self.COLORS["secondary"],
                                hover_color=self.COLORS["secondary_hover"],
                                text_color=self.COLORS["text_main"])
        self.worker = TranslationWorker(self.log_queue, self._on_done)
        self.worker.start()

    def _stop(self):
        if self.worker:
            self.worker.stop_flag = True
            # 일시정지 중이거나 대기 중이어도 즉시 빠져나오게 이벤트를 깨운다
            self.worker._pause_event.set()
        self.btn_stop.configure(state="disabled")
        self.btn_pause.configure(state="disabled")
        self._add_log("중지 요청됨 — 곧 종료됩니다.", "warn")

    def _pause(self):
        if self.worker and not self.worker.pause_flag:
            self.worker.pause()
            self.btn_pause.configure(text="RESUME", fg_color="#4a5568",
                                     hover_color="#2d3748", text_color="white")
            self._set_status("Paused", "#dd6b20")
        elif self.worker and self.worker.pause_flag:
            self.worker.resume()
            self.btn_pause.configure(text="PAUSE",
                                     fg_color=self.COLORS["secondary"],
                                     hover_color=self.COLORS["secondary_hover"],
                                     text_color=self.COLORS["text_main"])
            self._set_status("Infusing...", self.COLORS["accent"])

    def _build_lang_area(self):
        """언어 표시 영역 구성 — 번역 모드는 언어 라벨, 검수 모드는 원문→대상 선택."""
        for w in self.lang_area.winfo_children():
            w.destroy()
        mode = _current_mode()
        if mode in REVIEW_MODES:
            ctk.CTkLabel(self.lang_area, text="🔍",
                         font=ctk.CTkFont(size=12)).pack(side="left")
            ctk.CTkLabel(self.lang_area, text=" 검수 언어: ",
                         font=ctk.CTkFont(size=12, weight="bold"),
                         text_color=self.COLORS["accent"]).pack(side="left")
            labels = [LANG_LABELS.get(c, c) for c in REVIEW_LANGS]
            self._rev_label_to_code = {LANG_LABELS.get(c, c): c for c in REVIEW_LANGS}
            src = getattr(config, "REVIEW_SRC_LANG", "ko")
            tgt = getattr(config, "REVIEW_TGT_LANG", "es")
            # 무채색 플랫 드롭다운 — 카드 톤에 맞춰 초록 계열 제거
            menu_style = dict(
                values=labels, width=150, height=28, corner_radius=8,
                font=ctk.CTkFont(size=12),
                fg_color="#eef2f7",
                button_color="#e2e8f0",
                button_hover_color="#cbd5e0",
                text_color=self.COLORS["text_main"],
                dropdown_fg_color="#ffffff",
                dropdown_hover_color="#eef2f7",
                dropdown_text_color=self.COLORS["text_main"],
                command=lambda _v: self._on_review_lang())
            self._rev_src_var = tk.StringVar(value=LANG_LABELS.get(src, src))
            ctk.CTkOptionMenu(self.lang_area, variable=self._rev_src_var,
                              **menu_style).pack(side="left")
            ctk.CTkLabel(self.lang_area, text=" → ",
                         font=ctk.CTkFont(size=12, weight="bold"),
                         text_color=self.COLORS["text_sub"]).pack(side="left")
            self._rev_tgt_var = tk.StringVar(value=LANG_LABELS.get(tgt, tgt))
            ctk.CTkOptionMenu(self.lang_area, variable=self._rev_tgt_var,
                              **menu_style).pack(side="left")
        else:
            ctk.CTkLabel(self.lang_area, text="🌍",
                         font=ctk.CTkFont(size=12)).pack(side="left")
            _lang = getattr(config, "PROMPT_LANG", "")
            ctk.CTkLabel(
                self.lang_area,
                text=f" 번역 언어: {LANG_LABELS.get(_lang, _lang) or '(미선택)'}",
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color=self.COLORS["accent"]).pack(side="left")

    def _update_mode_buttons(self):
        """모드 세그먼트 표시 갱신 — 선택된 모드만 흰 알약 + 포인트 색 글자."""
        cur = _current_mode()
        for m, btn in self._mode_btns.items():
            if m == cur:
                btn.configure(fg_color="#ffffff", hover_color="#ffffff",
                              text_color=self.COLORS["accent"],
                              font=self._mode_font_bold)
            else:
                btn.configure(fg_color="transparent", hover_color="#e2e8f0",
                              text_color=self.COLORS["text_sub"],
                              font=self._mode_font)

    def _set_mode_selector_state(self, state):
        """실행 중 모드 전환 방지용 — 세그먼트 버튼 일괄 활성/비활성."""
        for btn in self._mode_btns.values():
            btn.configure(state=state)

    def _on_mode_change(self, mode):
        """모드 세그먼트 버튼 — 열 역할 프리셋 교체 + 언어 영역 갱신 + 저장."""
        if mode == _current_mode():
            return
        # 지금 화면의 열 역할을 '이전 모드' 프리셋에 보관한 뒤 새 모드 프리셋을 적용
        store_mode_columns()
        config.WORK_MODE = mode
        apply_mode_columns()
        save_settings()
        self._update_mode_buttons()
        self._build_lang_area()
        self._add_log(f"작업 모드 전환: {WORK_MODE_LABELS.get(mode, mode)}", "info")

    def _on_review_lang(self):
        """검수 원문/대상 언어 드롭다운 변경 — 즉시 config 반영·저장."""
        src = self._rev_label_to_code.get(self._rev_src_var.get(), "ko")
        tgt = self._rev_label_to_code.get(self._rev_tgt_var.get(), "es")
        config.REVIEW_SRC_LANG = src
        config.REVIEW_TGT_LANG = tgt
        save_settings()

    def _refresh_lang_label(self):
        """메인 화면 상태 카드의 언어 표시를 현재 config 기준으로 갱신"""
        if hasattr(self, "lang_area"):
            self._build_lang_area()

    def _open_settings(self):
        dlg = SettingsDialog(self)
        self.wait_window(dlg)
        self._refresh_lang_label()

    def _open_lang(self):
        dlg = LangDialog(self)
        self.wait_window(dlg)
        self._refresh_lang_label()

    def _open_prompt(self):
        PromptDialog(self)

    # ── 자동 업데이트 ────────────────────────────────────────────────────────

    def _check_update_async(self):
        """시작 시 호출 — 백그라운드 스레드로 확인 후, 있으면 메인스레드에서 안내."""
        def worker():
            try:
                import updater
                result = updater.check_for_update(timeout=12)
            except Exception:
                result = None
            # UI 갱신은 메인 스레드에서
            self.after(0, lambda: self._on_update_checked(result, silent=True))
        threading.Thread(target=worker, daemon=True).start()

    def _check_update_manual(self):
        """🔄 버튼 — 사용자가 직접 확인. 최신이어도 결과를 알려준다."""
        self._set_status("업데이트 확인 중...", self.COLORS["text_sub"])
        def worker():
            try:
                import updater
                result = updater.check_for_update(timeout=12)
            except Exception:
                result = None
            self.after(0, lambda: self._on_update_checked(result, silent=False))
        threading.Thread(target=worker, daemon=True).start()

    def _on_update_checked(self, result, silent):
        """확인 결과 처리. silent=True(자동)면 최신/오류 시 조용히 넘어감."""
        if result is None:
            if not silent:
                messagebox.showinfo("업데이트", "업데이트 서버에 연결하지 못했습니다.\n네트워크를 확인해주세요.")
            self._set_status("Ready", self.COLORS["text_sub"])
            return

        if not result["available"]:
            if not silent:
                messagebox.showinfo("업데이트", f"이미 최신 버전입니다. (v{result['local']})")
            self._set_status("Ready", self.COLORS["text_sub"])
            return

        # 업데이트 있음 → 항상 물어본다 (자동/수동 공통)
        size_mb = (result.get("size") or 0) / (1024 * 1024)
        msg = (f"새 버전이 있습니다.\n\n"
               f"  현재: v{result['local']}\n"
               f"  최신: v{result['version']}\n")
        if size_mb:
            msg += f"  다운로드 크기: 약 {size_mb:.1f} MB\n"
        notes = clean_release_notes(result.get("notes") or "")
        if notes:
            snippet = notes if len(notes) <= 300 else notes[:300] + "..."
            msg += f"\n[변경 내용]\n{snippet}\n"

        # 개발(.py) 모드에서는 exe 통째 교체가 불가능 → 안내만.
        if not result.get("self_update"):
            messagebox.showinfo(
                "업데이트 가능",
                msg + "\n\n개발 모드(.py 실행)에서는 자동 교체가 안 됩니다.\n"
                      "최신 코드는 git pull 로 받아주세요.")
            self._set_status("Ready", self.COLORS["text_sub"])
            return

        msg += "\n지금 업데이트하시겠습니까?\n(다운로드 후 자동으로 교체·재시작됩니다)"
        if not messagebox.askyesno("업데이트 가능", msg):
            self._set_status("Ready", self.COLORS["text_sub"])
            return

        self._do_update(result)

    def _do_update(self, result):
        """새 exe 다운로드 → 교체·재시작."""
        self._add_log("업데이트 다운로드를 시작합니다...", "info")
        self._set_status("업데이트 다운로드 중...", self.COLORS["accent"])

        def on_progress(done, total):
            if total:
                pct = done * 100 // total
                self.after(0, lambda: self._set_status(
                    f"다운로드 중... {pct}%", self.COLORS["accent"]))

        def worker():
            try:
                import updater
                path = updater.download_update(result, progress=on_progress)
                err = None
            except Exception as e:
                path, err = None, str(e)
            self.after(0, lambda: self._after_download(path, err))
        threading.Thread(target=worker, daemon=True).start()

    def _after_download(self, path, err):
        if err or not path:
            messagebox.showwarning(
                "업데이트 실패",
                f"다운로드에 실패했습니다.\n\n{err or '알 수 없는 오류'}\n\n"
                "잠시 후 다시 시도해주세요.")
            self._set_status("Ready", self.COLORS["text_sub"])
            return

        messagebox.showinfo("업데이트 준비 완료",
                            "다운로드가 끝났습니다.\n프로그램을 교체하고 재시작합니다.")
        try:
            import updater
            updater.apply_and_restart(path)
        except Exception as e:
            messagebox.showerror(
                "업데이트 실패",
                f"교체 단계에서 오류가 발생했습니다.\n\n{e}")
            self._set_status("Ready", self.COLORS["text_sub"])
            return
        # 새 exe(교체 담당)가 떴다. 이 프로세스가 살아 있으면 구 exe 파일이 잠겨
        # 교체가 안 되므로, 정리 없이 즉시 강제 종료해 잠금을 푼다.
        os._exit(0)

    def _clear_log(self):
        if not messagebox.askyesno("로그 삭제", "시스템 로그를 삭제하시겠습니까?"):
            return
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    def _animate(self):
        if self._is_waiting and self._waiting_msg:
            self._dot_count = (self._dot_count % 3) + 1
            dots = " ·" * self._dot_count
            self.status_lbl.configure(
                text=f"  {self._waiting_msg}{dots}",
                text_color="#dd6b20"
            )
            self.dot_lbl.configure(text_color="#dd6b20")
        self.after(500, self._animate)

    # ── UI 업데이트 ───────────────────────────────────────────────────────────

    def _add_log(self, msg, tag="info"):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", msg + "\n", tag)
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _set_status(self, text, dot_color="#9ece6a"):
        self.status_lbl.configure(text=f"  {text}")
        self.dot_lbl.configure(text_color=dot_color)

    def _on_done(self, processed, total, fail_lines):
        self.log_queue.put(("done", (processed, total, fail_lines)))

    def _poll(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                kind = msg[0]
                if kind == "log":
                    _, text, tag = msg
                    self._add_log(text, tag)
                elif kind == "progress":
                    _, (cur, total) = msg
                    ratio = cur / total if total else 0
                    self.progress.set(ratio)
                    pct = f"{ratio*100:.1f}%"
                    self.prog_lbl.configure(text=f"{cur:,} / {total:,} Rows  ({pct})")
                elif kind == "status":
                    _, (text, color) = msg
                    self._set_status(text, color)
                elif kind == "waiting":
                    _, msg_text = msg
                    self._waiting_msg = msg_text
                    self._is_waiting = True
                    self._dot_count = 0
                elif kind == "waiting_text":
                    # 베이스 문구만 갱신(경과 시간 표시). 상태/점 카운트는 유지.
                    _, msg_text = msg
                    if self._is_waiting:
                        self._waiting_msg = msg_text
                elif kind == "empty":
                    messagebox.showinfo(
                        "대상 없음",
                        "처리할 행을 찾지 못했습니다.\n\n다음을 확인해보세요:\n"
                        "• 결과열(결과 기입)이 이미 채워져 있지 않은지\n"
                        "• 시작 행 번호가 올바른지\n"
                        "• 원본/대상 열 역할(설정 → 열 설정)이 맞는지\n"
                        "• (검수 모드) '검수대상' 열에 검수할 번역문이 들어있는지\n"
                        "• '시트(탭) 이름'이 실제 작업 탭과 같은지")
                elif kind == "done_waiting":
                    self._is_waiting = False
                    self._waiting_msg = ""
                elif kind == "done":
                    _, (processed, total, fail_lines) = msg
                    self._is_waiting = False
                    self._waiting_msg = ""
                    self.btn_start.configure(state="normal")
                    self.btn_stop.configure(state="disabled")
                    self.btn_pause.configure(state="disabled")
                    self._set_mode_selector_state("normal")
                    DoneDialog(self, processed, total, fail_lines)
        except queue.Empty:
            pass
        self.after(100, self._poll)


# ── 진입점 ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import updater
    # 새 exe 가 --apply-update 모드로 실행되면 GUI 대신 교체만 수행하고 종료한다.
    if updater.APPLY_FLAG in sys.argv:
        idx = sys.argv.index(updater.APPLY_FLAG)
        target = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else ""
        if target:
            updater.perform_swap(target)
        sys.exit(0)

    # 교체 후 첫 실행이면 남은 다운로드 임시파일 정리
    updater.cleanup_after_update()

    app = App()
    app.mainloop()