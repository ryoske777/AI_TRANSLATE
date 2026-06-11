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
    group_consecutive_rows, format_batch, parse_response, is_empty, col_to_idx,
    list_prompt_langs, load_prompt, ensure_external_prompts, find_chrome,
    extract_spreadsheet_id, PROMPTS_DIR, LANG_LABELS,
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

        processed = 0
        total = 0
        driver = None
        try:
            self.status("Google Sheets 연결 중...", "#e0af68")
            self.log("Google Sheets 연결 중...")
            sheet = get_sheet()

            pending_rows = get_pending_rows(sheet)
            if not pending_rows:
                self.log("처리할 행이 없습니다.", "warn")
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
                    if attempt < 5:
                        self.log(f"Chrome 연결 시도 {attempt}/5 실패 — 2초 후 재시도...", "warn")
                        _t.sleep(2)
                    else:
                        raise Exception("Chrome 연결 실패 (5회 시도). Chrome을 다시 실행해주세요.")
            self.log("Chrome 연결 완료", "success")

            self.status("번역 진행 중...", "#9ece6a")
            self.send_count = 0
            groups = group_consecutive_rows(pending_rows)
            gi = 0
            bi = 0

            while gi < len(groups) and not self.stop_flag:
                group = groups[gi]

                if self.send_count == 0 or self.send_count >= config.MAX_SENDS_PER_CONVERSATION or self.force_new_conv:
                    # ── E열 특이사항 재검증 스윕 ──────────────────────
                    # 한글 포함/플레이스홀더 불일치 표시를 현재 C/D 기준으로 다시 보고,
                    # 실제로 정상이면 표시만 지운다. (무엇을 발견/검증했는지 로그로 노출)
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
                            self.log(f"재스캔 완료: {len(fresh)}행 / {len(groups)}개 그룹")
                    self.log(f"새 대화 시작 (누적: {processed}/{total}행)")
                    self.waiting("새 ChatGPT 페이지 로딩 중")
                    new_conversation(driver)
                    self.done_waiting()
                    self.log("페이지 로드 완료", "info")
                    self.waiting("고정 프롬프트 전송 중")
                    send_message(driver, config.FIXED_PROMPT)
                    wait_for_response(driver)
                    self.done_waiting()
                    self.send_count = 0
                    self.force_new_conv = False
                    self.log("고정 프롬프트 전송 완료", "success")

                batch = group[bi: bi + config.BATCH_SIZE]
                s_row, e_row = batch[0][0], batch[-1][0]
                label = " (구멍 그룹)" if len(group) < config.BATCH_SIZE else ""
                self.log(f"배치 전송: {s_row}~{e_row}행 ({len(batch)}행{label})")
                self.waiting(f"{s_row}~{e_row}행 번역 요청 중")
                send_message(driver, format_batch(batch))
                self.done_waiting()
                self.waiting(f"ChatGPT 응답 대기 중")
                wait_for_response(driver)
                self.done_waiting()

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

                    # ── 한글 감지 → 1회 즉시 재시도 ──────────────────
                    from main import has_korean, filter_korean_lines
                    korean_idxs = filter_korean_lines(lines)
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
                        wait_for_response(driver)
                        self.done_waiting()
                        retry_resp = extract_last_response(driver)
                        if retry_resp:
                            retry_lines, _ = parse_response(retry_resp, retry_batch)
                            for j, idx in enumerate(korean_idxs):
                                if j < len(retry_lines) and idx < len(lines):
                                    if retry_lines[j]:
                                        lines[idx] = retry_lines[j]
                        # (E열 '한글 포함' 표시/정리는 아래 reconcile_status에서 일괄 처리)

                    # ── 플레이스홀더 검증 → 1회 즉시 재시도 ──────────
                    if getattr(config, "PRESERVE_PLACEHOLDERS", True):
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
                                wait_for_response(driver)
                                self.done_waiting()
                                retry_resp = extract_last_response(driver)
                                if retry_resp:
                                    retry_lines, _ = parse_response(retry_resp, retry_batch)
                                    for j, idx in enumerate(ph_idxs):
                                        if j < len(retry_lines) and idx < len(lines):
                                            if retry_lines[j]:
                                                lines[idx] = retry_lines[j]

                    # ── E열 상태 최종 정리 (한글 + 플레이스홀더 통합) ──
                    # 최종 결과(lines)를 다시 검사해 불일치/한글은 표시하고,
                    # 정상이 된 행에 남아있던 자동 표시는 셀을 완전히 비운다.
                    mismatch_rows, korean_rows, cleared_rows = reconcile_status(
                        sheet, s_row, lines, ph_sources
                    )
                    for r in mismatch_rows:
                        self.log(f"📝 {r}행 → 플레이스홀더 불일치, E열: 플레이스홀더 불일치", "warn")
                    for r in korean_rows:
                        self.log(f"📝 {r}행 → 한글 포함, E열: 한글 포함", "warn")
                    for r in cleared_rows:
                        self.log(f"🧹 {r}행 → 정상, E열 표시 제거", "success")

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
                _t.sleep(_r.uniform(config.DELAY_MIN, config.DELAY_MAX))

                # 일시정지 대기
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

class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("설정")
        self.geometry("500x600")
        self.resizable(True, True)
        self.grab_set()
        self.after(50, self.lift)
        self._build()

    def _field(self, parent, label, value, row, wide=False):
        ctk.CTkLabel(parent, text=label, anchor="w").grid(
            row=row, column=0, padx=(0, 12), pady=6, sticky="w")
        var = tk.StringVar(value=str(value))
        w = 320 if wide else 200
        ctk.CTkEntry(parent, textvariable=var, width=w).grid(
            row=row, column=1, pady=6, sticky="w")
        return var

    def _build(self):
        scroll = ctk.CTkScrollableFrame(self)
        scroll.pack(fill="both", expand=True, padx=16, pady=(16, 0))

        # 스프레드시트
        ctk.CTkLabel(scroll, text="📊  스프레드시트",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(
            anchor="w", pady=(0, 6))
        f1 = ctk.CTkFrame(scroll, fg_color="transparent")
        f1.pack(fill="x")
        self.v_id    = self._field(f1, "스프레드시트 ID", config.SPREADSHEET_ID, 0, True)
        self.v_sheet = self._field(f1, "시트 이름",       config.SHEET_NAME,      1)
        self.v_start = self._field(f1, "시작 행 번호",    config.START_ROW,       2)

        ctk.CTkFrame(scroll, height=1, fg_color="#3a3a4a").pack(
            fill="x", pady=10)

        # 번역 설정
        ctk.CTkLabel(scroll, text="⚙️  번역 설정",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(
            anchor="w", pady=(0, 6))
        f2 = ctk.CTkFrame(scroll, fg_color="transparent")
        f2.pack(fill="x")
        self.v_batch = self._field(f2, "배치 크기 (행)",       config.BATCH_SIZE,                0)
        self.v_sends = self._field(f2, "대화당 최대 전송 횟수", config.MAX_SENDS_PER_CONVERSATION, 1)
        self.v_dmin  = self._field(f2, "딜레이 최소 (초)",      config.DELAY_MIN,                 2)
        self.v_dmax  = self._field(f2, "딜레이 최대 (초)",      config.DELAY_MAX,                 3)
        self.v_init  = self._field(f2, "응답 감지 시작 대기 (초)", getattr(config, "RESPONSE_INIT_WAIT", 2.0),     4)
        self.v_poll  = self._field(f2, "응답 폴링 간격 (초)",    getattr(config, "RESPONSE_POLL_INTERVAL", 0.5), 5)
        self.v_done  = self._field(f2, "응답 완료 후 대기 (초)", getattr(config, "RESPONSE_DONE_DELAY", 1.0),    6)

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

        # 열 설정
        ctk.CTkLabel(scroll, text="📋  열 설정",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(
            anchor="w", pady=(0, 6))

        ROLES = ["source", "ref", "placeholder", None]
        ROLE_LABELS = {"source": "원본", "ref": "참조", "placeholder": "플레이스홀더", None: "제외"}

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
            var = tk.StringVar(value=str(getattr(config, attr, None)))
            self.col_vars[attr] = var
            for role in ROLES:
                label = ROLE_LABELS[role]
                val = str(role)
                rb = ctk.CTkRadioButton(row_f, text=label, variable=var, value=val,
                                        width=90)
                rb.pack(side="left", padx=4)

        # 결과열
        res_f = ctk.CTkFrame(col_frame, fg_color="transparent")
        res_f.pack(fill="x", pady=(6, 0))
        ctk.CTkLabel(res_f, text="결과열", width=40, anchor="w").pack(side="left")
        self.v_result_col = tk.StringVar(value=getattr(config, "RESULT_COL", "D"))
        ctk.CTkEntry(res_f, textvariable=self.v_result_col, width=60).pack(side="left", padx=4)
        ctk.CTkLabel(res_f, text="(열 문자 입력: A, B, C, D ...)",
                     text_color="#888", font=ctk.CTkFont(size=11)).pack(side="left", padx=4)

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

    def _save(self):
        try:
            config.SPREADSHEET_ID             = extract_spreadsheet_id(self.v_id.get())
            config.SHEET_NAME                 = self.v_sheet.get().strip()
            config.START_ROW                  = int(self.v_start.get())
            config.BATCH_SIZE                 = int(self.v_batch.get())
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
            save_settings()
            self.destroy()
        except ValueError as e:
            messagebox.showerror("입력 오류", f"숫자 형식을 확인해주세요.\n{e}", parent=self)

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
        self.lang = getattr(config, "PROMPT_LANG", "")
        self.prompt_file = os.path.join(PROMPTS_DIR, f"{self.lang}.txt")

        ctk.CTkLabel(self,
                     text=f"프롬프트 편집 — 현재 언어: {LANG_LABELS.get(self.lang, self.lang) or '(미선택)'}",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(
            anchor="w", padx=20, pady=(16, 2))
        ctk.CTkLabel(self,
                     text="※ 여기엔 순수 번역 규칙만 작성하세요. 행 ID 규칙은 작업 시 자동으로 덧붙습니다.",
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
        self.title("번역 완료 — 결과 요약")
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
    data = {
        "SPREADSHEET_ID":             config.SPREADSHEET_ID,
        "SHEET_NAME":                 config.SHEET_NAME,
        "START_ROW":                  config.START_ROW,
        "BATCH_SIZE":                 config.BATCH_SIZE,
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
        self.v_id    = self._row(sf, "스프레드시트 ID", config.SPREADSHEET_ID, 0)
        self.v_sheet = self._row(sf, "시트 이름",       config.SHEET_NAME,      1)
        self.v_start = self._row(sf, "시작 행 번호",    config.START_ROW,       2)

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
        ctk.CTkLabel(scroll,
                     text="※ 구글 서비스 계정 키(.json)를 선택하면 프로그램 폴더로 복사됩니다.\n"
                          "   해당 계정 이메일에 스프레드시트 '공유'가 되어 있어야 합니다.",
                     text_color="#888", font=ctk.CTkFont(size=11)).pack(anchor="w", padx=4, pady=(0, 8))

        # 3) 기본 번역 설정
        ctk.CTkLabel(scroll, text="③ 기본 번역 설정",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w", pady=(6, 6))
        tf = ctk.CTkFrame(scroll, fg_color="transparent")
        tf.pack(fill="x")
        self.v_batch  = self._row(tf, "배치 크기 (행)", config.BATCH_SIZE, 0)
        self.v_result = self._row(tf, "결과열 (A,B,C,D...)", getattr(config, "RESULT_COL", "D"), 1)

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

        # 현재 선택된 번역 언어 표시 (RUN 전에 무슨 언어로 도는지 한눈에)
        lang_row = ctk.CTkFrame(card, fg_color="transparent")
        lang_row.pack(fill="x", padx=20, pady=(0, 2))
        ctk.CTkLabel(lang_row, text="🌍",
                     font=ctk.CTkFont(size=12)).pack(side="left")
        self.lang_lbl = ctk.CTkLabel(
            lang_row, text=f" 번역 언어: {LANG_LABELS.get(getattr(config, 'PROMPT_LANG', ''), getattr(config, 'PROMPT_LANG', '')) or '(미선택)'}",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=self.COLORS["accent"])
        self.lang_lbl.pack(side="left")

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

        # 선택된 언어 프롬프트 로드 (+ ID 규칙은 load_prompt가 자동 주입)
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

        self.btn_pause.configure(state="normal", text="PAUSE",
                                fg_color=self.COLORS["secondary"],
                                hover_color=self.COLORS["secondary_hover"],
                                text_color=self.COLORS["text_main"])
        self.worker = TranslationWorker(self.log_queue, self._on_done)
        self.worker.start()

    def _stop(self):
        if self.worker:
            self.worker.stop_flag = True
        self.btn_stop.configure(state="disabled")
        self.btn_pause.configure(state="disabled")
        self._add_log("중지 요청됨 — 현재 배치 완료 후 종료됩니다.", "warn")

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

    def _refresh_lang_label(self):
        """메인 화면 상태 카드의 '번역 언어' 표시를 현재 config 기준으로 갱신"""
        if hasattr(self, "lang_lbl"):
            _lang = getattr(config, "PROMPT_LANG", "")
            self.lang_lbl.configure(
                text=f" 번역 언어: {LANG_LABELS.get(_lang, _lang) or '(미선택)'}")

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
        notes = (result.get("notes") or "").strip()
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