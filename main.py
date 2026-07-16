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

    # 통합/삭제된 프롬프트가 외부 폴더에 남아 있으면 정리한다.
    for name in DEPRECATED_PROMPTS:
        p = os.path.join(dst, f"{name}.txt")
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass

# UI 라디오에 표시할 순서 (파일명 = 코드, 표시명은 LANG_LABELS 참조)
# 파일명을 ASCII로 둬서 OS/브라우저 어디서도 깨지지 않게 한다.
PROMPT_LANGS = [
    "en", "zh_cn", "th",
    "pt", "es", "de",
    "fr", "id", "tr",
]

# 코드 → 화면 표시용 한글 라벨
# es 는 유럽 서비스 기준, pt 는 브라질(pt-BR) 기준 프롬프트라 지역 변형을 라벨에 명시한다.
# es_la/pt_pt 는 검수 전용 변형 코드 (번역 프롬프트 파일 없음).
LANG_LABELS = {
    "ko": "한국어",
    "es": "스페인어(유럽)", "es_la": "스페인어(중남미)", "en": "영어",
    "fr": "프랑스어", "de": "독일어",
    "pt": "포르투갈어(브라질)", "pt_pt": "포르투갈어(유럽)",
    "tr": "튀르키예어", "id": "인도네시아어", "zh_cn": "중국어(간체)", "th": "태국어",
}

# ── 작업 모드 (번역 / 검수) ──────────────────────────────────────────────────

WORK_MODE_LABELS = {
    "translate":       "번역",
    "review_glossary": "용어집 검수",
    "review_general":  "일반 검수",
}
REVIEW_MODES = ("review_glossary", "review_general")

# 검수 모드에서 고를 수 있는 언어 (원문/대상 공통 — 한국어 + 지역 변형 포함)
# es_la/pt_pt 는 검수 전용 변형: 같은 언어라도 지역에 따라 어휘·문법이 달라
# '어느 지역 기준으로 검수할지'를 명확히 고를 수 있게 분리한다.
REVIEW_LANGS = [
    "ko", "en",
    "es", "es_la",
    "pt", "pt_pt",
    "de", "fr", "tr", "id", "zh_cn", "th",
]

# 검수 프롬프트에 주입되는 언어 설명 — 지역 변형 기준을 구체적으로 명시한다.
# 여기 문구가 그대로 프롬프트에 들어가므로, 무엇을 '지적 대상'으로 볼지도 함께 적는다.
REVIEW_LANG_DESC = {
    "ko":    "한국어",
    "en":    "영어",
    "es":    ("스페인어 — 반드시 유럽 스페인어(스페인 본토, es-ES) 기준으로 판단: "
              "스페인 본토의 어휘·정서법, 2인칭 복수 vosotros 계열 활용 사용. "
              "중남미식 스페인어 표현(ustedes 일반화, 중남미 고유 어휘 등)이나 "
              "포르투갈어 단어·철자가 섞여 있으면 반드시 지적 대상"),
    "es_la": ("스페인어 — 중남미(라틴아메리카, es-419) 기준: 중남미에서 널리 통용되는 "
              "중립적 스페인어. 스페인 본토에서만 쓰는 표현(vosotros 계열 등)이나 "
              "포르투갈어 단어가 섞여 있으면 지적 대상"),
    "pt":    ("포르투갈어 — 반드시 브라질 포르투갈어(pt-BR) 기준으로 판단: "
              "브라질 정서법·어휘, você 계열 활용 사용. "
              "유럽 포르투갈어식 어휘·철자·표현이나 스페인어 단어가 섞여 있으면 반드시 지적 대상"),
    "pt_pt": ("포르투갈어 — 유럽 포르투갈어(포르투갈 본토, pt-PT) 기준. "
              "브라질식 어휘·철자·표현이나 스페인어 단어가 섞여 있으면 지적 대상"),
    "fr":    "프랑스어 — 프랑스 본토(fr-FR) 표준 기준",
    "de":    "독일어 — 독일 표준(de-DE) 기준",
    "tr":    "튀르키예어 — 현대 표준 튀르키예어 기준",
    "id":    "인도네시아어 — 표준 인도네시아어(Bahasa Indonesia) 기준",
    "zh_cn": "중국어 간체 — 중국 본토 표준(푸퉁화, zh-CN) 기준",
    "th":    "태국어 — 표준 태국어 기준",
}

# 더 이상 쓰지 않는(통합/삭제된) 프롬프트 — 외부 폴더에 남아 있으면 정리한다.
DEPRECATED_PROMPTS = {"es_general"}

# 코드가 모든 번역 프롬프트 끝에 자동으로 붙이는 플레이스홀더 보존 규칙.
# 프롬프트 파일은 사용자가 언어별로 자유 편집하므로, 시스템이 반드시 보장해야
# 하는 토큰 보존 규칙은 파일이 아니라 코드가 단일 지점에서 주입한다.
# (PLACEHOLDER_RE / _CURLY_CODE_RE 검증 로직과 짝을 이루는 규칙)
PLACEHOLDER_RULE_BLOCK = """

────────────────────────────────
[ 플레이스홀더·코드 보존 규칙 — 시스템 필수 (번역 규칙보다 우선) ]
────────────────────────────────

- «T:...» 형태(길리메 « » 포함)와 {...} 형태(중괄호, 예: {CL:3}, {CL:0})는
  게임 엔진이 문자 그대로 읽는 코드입니다. 번역 대상이 아닙니다.
- 이 토큰들은 문자 하나 다르지 않게 통째로 복사하여 출력에 유지하십시오.
  내부 텍스트의 번역·요약·생략·재작성, 콜론(:) 제거, 첫 단어 삭제 등
  어떤 부분 수정도 금지입니다.
  (잘못된 예: «T:Paquete maestro E» → «T maestro E» / «T:Ubicación:» → «Tón:»)
- 입력에 «T:숫자» 형태(예: «T:7»)의 토큰이 있으면 그것은 마스킹된 코드입니다.
  출력에도 정확히 같은 «T:숫자» 를 쓰십시오. 숫자를 바꾸거나, 참고표의 내용으로
  풀어 쓰거나, « » 안에 다른 텍스트를 적지 마십시오.
- 원본에 없는 토큰을 새로 만들지 마십시오. 원본 토큰의 종류·개수와 출력
  토큰의 종류·개수는 정확히 일치해야 합니다. (위치만 문장에 맞게 이동 가능)
- 이 규칙 위반은 게임 오류로 직결됩니다. 문장의 자연스러움보다 이 규칙이 우선합니다.
"""

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
    available -= DEPRECATED_PROMPTS   # 통합/삭제된 항목은 목록에서 제외
    available = {a for a in available if not a.startswith("review_")}  # 검수 템플릿 제외
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
    return body + PLACEHOLDER_RULE_BLOCK + ID_RULE_BLOCK


# 검수 모드용 행 ID 규칙 — 출력이 '번역'이 아니라 '검수 결과'라는 점만 다르다.
REVIEW_ID_RULE_BLOCK = """

────────────────────────────────
[ 행 식별자(ID) 규칙 — 시스템 필수 (다른 모든 출력 규칙에 우선) ]
────────────────────────────────

- 입력의 각 줄은 맨 앞에 'R숫자' 식별자가 탭(TAB)으로 붙어 제공됩니다.
  예) R12<탭>SRC=원문<탭>CAT=카테고리<탭>TGT=검수할 번역
- 출력도 각 줄 맨 앞에 입력과 '완전히 동일한' R숫자를 붙이고, 탭(TAB) 뒤에 검수 결과를 적으십시오.
  예) R12<탭>OK     /     R12<탭>수정: ... | 사유: ...
- R숫자 식별자는 변경·삭제하지 말고, 숫자도 절대 바꾸지 마십시오. (그대로 복사)
- 입력 한 줄당 출력도 정확히 한 줄. 줄을 합치거나 쪼개지 마십시오.
- 출력 순서는 입력과 동일하게 유지하되, 매칭 기준은 R숫자입니다.
"""


def load_review_prompt(mode, src_lang, tgt_lang):
    """검수 모드 프롬프트 로드 — prompts/{mode}.txt 를 읽어 언어 토큰을 치환해 반환.

    템플릿 안의 {SRC_LANG} / {TGT_LANG} 토큰을 선택된 언어 설명으로 바꾸고,
    끝에 검수용 행 ID 규칙을 자동 주입한다. 파일이 없으면 빈 문자열 + 경고.
    """
    if mode not in REVIEW_MODES:
        return ""
    path = os.path.join(PROMPTS_DIR, f"{mode}.txt")
    if not os.path.exists(path):
        print(f"⚠️  검수 프롬프트 파일이 없습니다: {path}")
        return ""
    with open(path, "r", encoding="utf-8") as f:
        body = f.read().strip()
    src = REVIEW_LANG_DESC.get(src_lang, LANG_LABELS.get(src_lang, src_lang))
    tgt = REVIEW_LANG_DESC.get(tgt_lang, LANG_LABELS.get(tgt_lang, tgt_lang))
    body = body.replace("{SRC_LANG}", src).replace("{TGT_LANG}", tgt)
    return body + REVIEW_ID_RULE_BLOCK


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


def get_service_account_email():
    """credentials.json 에서 서비스 계정 이메일(client_email)을 읽어 반환. 없으면 ''."""
    p = paths.app_path("credentials.json")
    try:
        with open(p, "r", encoding="utf-8") as f:
            return (json.load(f).get("client_email") or "").strip()
    except Exception:
        return ""


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


def test_connection():
    """현재 설정으로 시트 연결을 즉시 점검한다. (성공여부, 메시지) 반환.

    UI의 '연결 테스트' 버튼이 호출한다. 네트워크 작업이므로 호출 측에서 스레드로 돌린다.
    """
    if not os.path.exists(paths.app_path("credentials.json")):
        return False, "credentials.json 파일이 없습니다. 설정/마법사에서 먼저 지정하세요."
    if not (config.SPREADSHEET_ID or "").strip():
        return False, "스프레드시트 주소(또는 ID)가 비어 있습니다."
    try:
        sheet = get_sheet()
    except Exception as e:
        return False, str(e)
    title = getattr(sheet, "title", config.SHEET_NAME)
    try:
        pending_txt = f"\n번역 대기: {len(get_pending_rows(sheet))}행"
    except Exception:
        pending_txt = ""
    email = get_service_account_email()
    msg = f"연결 성공 ✓\n시트(탭): {title}{pending_txt}"
    if email:
        msg += f"\n서비스 계정: {email}"
    return True, msg


def is_empty(val):
    """빈 값 또는 nan 여부 확인"""
    v = val.strip().lower()
    return v == "" or v == "nan"


def col_to_idx(letter):
    """열 문자 → 0-based 인덱스 (A→0, B→1, ... Z→25, AA→26, AB→27 ...)"""
    idx = 0
    for ch in str(letter).strip().upper():
        if not ("A" <= ch <= "Z"):
            continue
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx - 1 if idx > 0 else 0


def idx_to_col(idx):
    """0-based 인덱스 → 열 문자 (0→A, 25→Z, 26→AA ...)"""
    idx += 1
    s = ""
    while idx > 0:
        idx, r = divmod(idx - 1, 26)
        s = chr(ord("A") + r) + s
    return s


def next_col_letter(col):
    """열 문자의 바로 다음 열 (D→E, Z→AA)"""
    return idx_to_col(col_to_idx(col) + 1)


def get_col_roles():
    """config에서 열 역할 설정 반환 [(col_idx, role), ...] 순서 보장"""
    roles = []
    for col, role_attr in [("A", "COL_A_ROLE"), ("B", "COL_B_ROLE"), ("C", "COL_C_ROLE")]:
        role = getattr(config, role_attr, None)
        if role:
            roles.append((col_to_idx(col), role))
    return roles


def get_pending_rows(sheet):
    """설정된 입력열 기준으로 미처리 행 반환 (nan 포함 공백 처리)

    반환 튜플: (행번호, source, ref, placeholder, category, review)
    검수 모드(WORK_MODE=review_*)에서는 '검수대상(review)' 열이 비어 있는 행은
    검수할 번역문이 없으므로 건너뛴다.
    """
    all_values = sheet.get_all_values()
    pending = []
    skipped = 0
    no_target = 0
    result_idx = col_to_idx(getattr(config, 'RESULT_COL', 'D'))
    col_roles = get_col_roles()
    is_review = getattr(config, "WORK_MODE", "translate") in REVIEW_MODES

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

        # 검수 모드: 검수대상 번역문이 없는 행은 처리 불가 → 건너뜀
        if is_review and not vals.get("review"):
            no_target += 1
            continue

        if is_empty(d):
            pending.append((
                i,
                vals.get("source", ""),
                vals.get("ref", ""),
                vals.get("placeholder", ""),
                vals.get("category", ""),
                vals.get("review", ""),
            ))
        else:
            skipped += 1
    if skipped > 0:
        print(f"  → {skipped}행 건너뜀 (결과열 이미 완료)")
    if no_target > 0:
        print(f"  → {no_target}행 건너뜀 (검수대상 열이 빈 칸)")
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
        # 창이 최소화/가려져도 페이지가 throttle되지 않게 (CDP 입력과 함께 백그라운드 동작 보장)
        "--disable-background-timer-throttling",
        "--disable-renderer-backgrounding",
        "--disable-backgrounding-occluded-windows",
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

    try:
        driver = webdriver.Chrome(options=options)
    except Exception as e:
        raise RuntimeError(
            "Chrome 드라이버(chromedriver) 시작 실패. 보통 인터넷에서 드라이버를 "
            "자동 설치하지 못할 때 발생합니다. 인터넷 연결을 확인하고 다시 시도하세요.\n"
            f"원인: {e}")
    if not config.USE_REMOTE_DEBUGGING:
        driver.maximize_window()

    # 창이 최소화/백그라운드여도 페이지가 멈추지 않게: 렌더러에 '항상 포커스됨'을
    # 에뮬레이트한다. (이미 떠 있던 크롬을 재사용해도 연결 세션에 즉시 적용됨)
    try:
        driver.execute_cdp_cmd("Emulation.setFocusEmulationEnabled", {"enabled": True})
    except Exception:
        pass

    return driver


def new_conversation(driver):
    """ChatGPT 새 대화 시작"""
    driver.get("https://chatgpt.com/")
    time.sleep(4)
    print("  → 새 대화 페이지 로드 완료")


def _insert_text_cdp(driver, textarea, text):
    """CDP(Input.insertText)로 입력창에 텍스트를 넣는다. 성공 시 True.

    DOM 레벨 포커스 + 전체선택 후 신뢰된 입력 이벤트로 교체하므로, 창이
    최소화/백그라운드/다른 가상 데스크톱에 있어도 동작한다. (OS 창 포커스 불필요)
    """
    try:
        # 포커스 + 기존 내용 전체 선택 (DOM 셀렉션 — OS 포커스와 무관)
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
        # 선택 영역을 새 텍스트로 교체 (beforeinput/input 이벤트 동반 → 에디터가 인식)
        driver.execute_cdp_cmd("Input.insertText", {"text": text})
        time.sleep(0.3)
        current = driver.execute_script("return arguments[0].innerText;", textarea)
        return bool(current and current.strip())
    except Exception:
        return False


def _click_send_button(driver, textarea):
    """전송 버튼 클릭. JS 클릭(백그라운드 안전) → Selenium 클릭 → Enter 순서로 시도."""
    sel = "button[data-testid='send-button']"
    # 1) JS 클릭 — 창이 안 보여도 동작
    try:
        btns = driver.find_elements(By.CSS_SELECTOR, sel)
        if btns:
            driver.execute_script("arguments[0].click();", btns[0])
            return
    except Exception:
        pass
    # 2) Selenium 클릭
    try:
        btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, sel)))
        btn.click()
        return
    except Exception:
        pass
    # 3) 최후 — Enter (포커스 필요, 백그라운드에선 실패할 수 있음)
    try:
        textarea.send_keys(Keys.RETURN)
    except Exception:
        pass


def send_message(driver, text):
    """ChatGPT 입력창에 텍스트 입력 후 전송.

    1순위: CDP 입력(최소화/백그라운드에서도 동작). 실패 시 기존 방식(포커스 필요)으로 폴백.
    """
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

    # ── 1순위: CDP 입력 ─────────────────────────────────────────────
    if _insert_text_cdp(driver, textarea, text):
        time.sleep(0.3)
    else:
        # ── 폴백: 기존 방식 (창이 활성화돼 있어야 함) ─────────────────
        textarea.click()
        time.sleep(0.5)
        textarea.send_keys(Keys.CONTROL + "a")
        textarea.send_keys(Keys.DELETE)
        time.sleep(0.3)
        # JS execCommand로 직접 입력 (클립보드 붙여넣기 시 첨부로 변환되는 문제 방지)
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

    # 전송
    _click_send_button(driver, textarea)

    # 전송 후 랜덤 딜레이 (봇 감지 방지)
    time.sleep(random.uniform(1.5, 3.5))
    print("  → 메시지 전송 완료, 응답 대기 중...")


def wait_for_response(driver, timeout=300, should_stop=None, on_tick=None):
    """ChatGPT 응답 완료까지 대기 — 출현 후 소멸 방식.

    should_stop: 호출 시 True면 즉시 중단하고 "STOPPED" 반환 (STOP 즉시 반응용)
    on_tick:     매 폴링마다 on_tick(경과초)를 호출 (진행 상태 표시용)
    """
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

    stop_selectors = [
        "button[data-testid='stop-button']",
        "button[aria-label='Stop streaming']",
        "button[aria-label='Stop generating']",
    ]

    # 1단계: Stop 버튼이 나타날 때까지 대기 (최대 30초)
    appeared = False
    start = time.time()
    while time.time() - start < 30:
        if _stopped():
            return "STOPPED"
        for sel in stop_selectors:
            elems = driver.find_elements(By.CSS_SELECTOR, sel)
            if elems and elems[0].is_displayed():
                appeared = True
                break
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
    """ChatGPT 입력창이 보이면 로그인된 것으로 판단한다.

    new_conversation 등으로 chatgpt.com 이 이미 로드된 상태에서 호출한다.
    timeout 초 안에 입력창이 안 나타나면 (로그인 안 됨/페이지 미로드로) False.
    """
    selectors = [
        (By.ID, "prompt-textarea"),
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


# 검수 판정 '수정: <제안> | 사유: ...' 에서 제안 부분만 떼어내는 패턴
# 콜론은 반각(:)·전각(：) 둘 다 허용 (모델이 섞어 쓰는 경우 대비)
_REVIEW_FIX_RE = re.compile(r"^\s*수정\s*[:：]\s*(.+?)\s*(?:\|.*)?$", re.S)


def parse_review_verdict(verdict, original):
    """검수 응답 한 줄을 (최종 결과, 비고) 로 분리한다.

    - 'OK'              → (original 그대로, "OK")     : 문제 없음 → 결과열에 기존 번역 유지
    - '수정: X | 사유: …' → (X, 판정 전체)             : 결과열엔 수정안만, 비고열엔 판정+사유
    - 빈 값(응답 누락)    → ("", "")                    : 결과열 빈 칸 → 재실행 시 재검수
    - 그 외(형식 이탈)    → ("", 판정 전체)             : 비고만 남기고 결과열은 비워 재검수 유도
    """
    v = (verdict or "").strip()
    if not v:
        return "", ""
    core = v.strip("`").strip().rstrip(".").strip()
    if core.upper() == "OK":
        return original, "OK"
    m = _REVIEW_FIX_RE.match(v)
    if m and m.group(1).strip():
        return m.group(1).strip(), v
    return "", v


def write_review_notes(sheet, start_row, notes):
    """검수 비고(OK / 수정+사유)를 결과열 바로 다음 열에 일괄 기입.

    빈 비고(응답 누락 행)는 건드리지 않는다. 결과열이 D면 비고는 E에 들어간다.
    """
    note_col = next_col_letter(getattr(config, "RESULT_COL", "D"))
    updates = [
        {"range": f"{note_col}{start_row + i}", "values": [[n]]}
        for i, n in enumerate(notes) if n
    ]
    if not updates:
        return
    try:
        sheet.batch_update(updates)
        print(f"  → 검수 비고 기입 완료 ({note_col}열, {len(updates)}행)")
    except Exception as e:
        print(f"  ❌ 검수 비고 기입 실패: {e}")


def format_review_batch(batch_rows):
    """
    검수 배치를 ChatGPT 입력 형식으로 변환 — 각 필드에 태그(SRC/CAT/TGT/REF)를 붙인다.

    열 순서가 사용자마다 달라도 모델이 필드 역할을 오해하지 않도록,
    값 앞에 역할 태그를 명시한다. 행 ID(R{행번호})는 번역 배치와 동일하게 맨 앞.
      예) R12<탭>SRC=마그누스 엑소시스무스<탭>CAT=스킬<탭>TGT=Magnus Exorcismus
    """
    col_roles = get_col_roles()
    role_order = [role for _, role in col_roles]

    role_to_key = {"source": 1, "ref": 2, "placeholder": 3, "category": 4, "review": 5}
    role_tags = {"source": "SRC", "ref": "REF", "placeholder": "PH",
                 "category": "CAT", "review": "TGT"}
    lines = []
    for row in batch_rows:
        parts = [row_id(row[0])]  # 맨 앞에 행 ID
        for role in role_order:
            key = role_to_key.get(role)
            if key is None or key >= len(row):
                continue
            parts.append(f"{role_tags[role]}={sanitize_cell(row[key])}")
        lines.append("\t".join(parts))
    return "\n".join(lines)


# RO 로컬라이제이션 플레이스홀더 형식: «T:내용»
#   - «, »는 길리메(U+00AB, U+00BB)
#   - T: 가 표시자, 한 문장에 여러 번 등장 가능, 중첩은 없음
PLACEHOLDER_RE = re.compile(r'«T:[^«»]*»')

# 게임 엔진 제어 코드 형식: {내용}  (예: {CL:3}, {CL:0}, {CL:4})
# 번역에서 그대로 보존돼야 하는 두 번째 토큰 부류 — 검증 다중집합에 포함한다.
_CURLY_CODE_RE = re.compile(r'\{[^{}]*\}')

# 보존 대상 토큰 전체(«T:...» + {...})를 등장 순서대로 뽑는 패턴
_PRESERVE_TOKEN_RE = re.compile(r'«T:[^«»]*»|\{[^{}]*\}')


# get_pending_rows 행 튜플에서 번역 대상(placeholder 역할) 열의 인덱스
_PH_CELL_IDX = 3


# E열 상태 문구 (UI·CLI 공용 — main_ui 가 import 해서 사용)
PH_MISMATCH_MARK = "플레이스홀더 불일치"
KOREAN_MARK = "한글 포함"
# 자동으로 관리하는 표시들 (이 목록에 있는 값만 자동 정리/제거 대상. 사용자 메모는 보존)
MANAGED_MARKS = (PH_MISMATCH_MARK, KOREAN_MARK)


def extract_placeholders(text):
    """텍스트에서 플레이스홀더를 모두 추출 (출현 순서 유지)"""
    if not text:
        return []
    return PLACEHOLDER_RE.findall(text)


def extract_preserve_tokens(text):
    """보존 대상 토큰(«T:...» + {...})을 모두 추출 (출현 순서 유지)"""
    if not text:
        return []
    return _PRESERVE_TOKEN_RE.findall(text)


def check_placeholder_match(source, translated):
    """원본과 번역의 보존 토큰(«T:...», {...}) 다중집합이 일치하는지 검사.
    원본에 토큰이 없으면 True (검증 대상 아님).

    두 부류는 형식이 겹치지 않으므로 합집합 다중집합 비교 한 번이면
    «T:...» 와 {...} 각각의 다중집합 일치와 동치다."""
    if not source:
        return True
    src = extract_preserve_tokens(source)
    if not src:
        return True
    tgt = extract_preserve_tokens(translated or "")
    return sorted(src) == sorted(tgt)


def filter_placeholder_mismatch(sources, translations):
    """플레이스홀더가 불일치한 행의 인덱스 리스트"""
    out = []
    for i, src in enumerate(sources):
        tgt = translations[i] if i < len(translations) else ""
        if not check_placeholder_match(src, tgt):
            out.append(i)
    return out


def batch_placeholder_sources(batch_rows):
    """배치 행 튜플에서 플레이스홀더 원본(placeholder 역할 열) 값 리스트를 꺼낸다.

    검증 원본은 시트를 다시 읽지 않고 배치가 이미 들고 있는 값을 쓴다.
    (시트 재읽기가 실패하면 빈 값과 비교하게 되어 훼손된 번역이
    '일치'로 조용히 통과하던 문제를 원천 제거)
    """
    return [row[_PH_CELL_IDX] if len(row) > _PH_CELL_IDX else ""
            for row in batch_rows]


# ── 플레이스홀더 로컬 자동 복구 ──────────────────────────────────────────────
# 모델이 훼손한 플레이스홀더를 ChatGPT 재번역 왕복 없이 코드가 즉시 되돌린다.
# 복구 결과는 반드시 check_placeholder_match 를 통과해야만 채택하므로
# 검증 기준 자체는 전혀 완화되지 않는다. 복구 못 하면 기존처럼 재번역/E열 표시.

# 형식만 살짝 깨진 마스킹 번호 토큰: «T1», «T : 1», «T:1 », «T.1» 등
_BROKEN_NUM_TOKEN_RE = re.compile(r'«\s*T\s*[:.,;]?\s*(\d+)\s*»')

# 길리메 묶음 전체 (유효/훼손 구분 전 단계)
_GUILLEMET_SPAN_RE = re.compile(r'«[^«»]*»')


def repair_masked_tokens(lines, mapping):
    """형식이 살짝 깨진 «T:번호» 토큰을 원형으로 정규화한다.

    «T1», «T : 1», «T:1 » → «T:1». 그 번호가 mapping 에 실제로 존재할 때만
    복구하므로 오탐이 없다. unmask_placeholders() 직전에 호출한다.
    """
    if not mapping:
        return lines

    def repl(m):
        token = f"«T:{m.group(1)}»"
        return token if token in mapping else m.group(0)

    return [_BROKEN_NUM_TOKEN_RE.sub(repl, ln) if ln else ln for ln in lines]


def _candidate_fragment(span):
    """훼손 후보 «T...» 스팬에서 원문 대조용 조각을 뽑는다.

    «Tón:» → 'ón:', «T Máx.:» → 'Máx.:', «T» → '', «T:Descripción» → 'Descripción'
    T 마커가 없는 일반 길리메 인용(«cita» 등)은 후보가 아니므로 None.
    """
    inner = span[1:-1]
    if not inner.startswith("T"):
        return None
    return inner[1:].lstrip(":").strip()


def repair_placeholder_line(source, translated):
    """훼손된 플레이스홀더를 원본 기준으로 로컬 복구한 줄을 반환. 복구 불가면 None.

    1) 원본 토큰과 완전히 일치하는 스팬은 정상으로 소진
    2) «T 로 시작하지만 형식이 깨진 스팬을, '빠진 토큰' 중 내용 조각이
       유일하게 들어맞는 것과 매칭 (예: «Tón:» ↔ «T:Descripción:»)
    3) 남은 후보와 남은 빠진 토큰의 개수가 같으면 등장 순서대로 배정
    복구 결과가 다중집합 검증을 통과할 때만 반환한다. 애매하면 손대지 않는다.
    """
    if not source or not translated:
        return None
    src_tokens = extract_placeholders(source)
    if not src_tokens:
        return None

    from collections import Counter
    remaining = Counter(src_tokens)

    # 출력의 길리메 스팬을 순회: 정상 토큰은 소진, 나머지 «T… 는 복구 후보
    spans = list(_GUILLEMET_SPAN_RE.finditer(translated))
    consumed = Counter()
    candidates = []  # [스팬 인덱스, 대조 조각, 배정된 원본 토큰|None]
    for si, m in enumerate(spans):
        span = m.group(0)
        if consumed[span] < remaining.get(span, 0):
            consumed[span] += 1
            continue
        frag = _candidate_fragment(span)
        if frag is None:
            continue  # 일반 인용부 등 — 건드리지 않음
        candidates.append([si, frag, None])

    if not candidates:
        return None

    # 빠진 토큰 목록 (원본 등장 순서, 중복 포함)
    missing = []
    used = Counter()
    for t in src_tokens:
        if used[t] < consumed.get(t, 0):
            used[t] += 1
        else:
            missing.append(t)
    if not missing:
        return None

    # 1차: 내용 조각이 유일하게 들어맞는 빠진 토큰과 매칭
    unassigned = list(range(len(missing)))
    for cand in candidates:
        frag = cand[1]
        if not frag:
            continue
        f = frag.casefold()
        hits = [k for k in unassigned if f in missing[k][3:-1].casefold()]
        # 들어맞는 대상이 전부 같은 토큰이면 사실상 유일 매칭
        if hits and len({missing[k] for k in hits}) == 1:
            cand[2] = missing[hits[0]]
            unassigned.remove(hits[0])

    # 2차: 남은 후보 수 == 남은 빠진 토큰 수 → 등장 순서대로 배정
    left = [c for c in candidates if c[2] is None]
    if left:
        if len(left) != len(unassigned):
            return None
        for c, k in zip(left, unassigned):
            c[2] = missing[k]

    # 치환 적용 (스팬 위치 기준 — 같은 모양의 훼손 스팬도 각자 제 토큰으로)
    repl_by_span = {c[0]: c[2] for c in candidates}
    out, last = [], 0
    for si, m in enumerate(spans):
        if si in repl_by_span:
            out.append(translated[last:m.start()])
            out.append(repl_by_span[si])
            last = m.end()
    out.append(translated[last:])
    repaired = "".join(out)

    # 최종 안전망: 엄격한 다중집합 검증을 통과할 때만 채택
    return repaired if check_placeholder_match(source, repaired) else None


def repair_placeholder_lines(sources, lines, idxs=None):
    """불일치 행들을 로컬 복구한다. 반환: (새 lines, 복구 성공한 인덱스 리스트)"""
    if idxs is None:
        idxs = filter_placeholder_mismatch(sources, lines)
    new_lines = list(lines)
    repaired = []
    for i in idxs:
        src = sources[i] if i < len(sources) else ""
        cur = new_lines[i] if i < len(new_lines) else ""
        fixed = repair_placeholder_line(src, cur)
        if fixed is not None:
            new_lines[i] = fixed
            repaired.append(i)
    return new_lines, repaired


def mask_placeholders_in_batch(batch_rows):
    """번역 대상 열(placeholder 역할)의 «T:...» 를 «T:번호» 토큰으로 치환한다.

    모델이 플레이스홀더 내부 텍스트를 번역·축약·변형하는 사고(예:
    «T:Asistencia diaria» → «T diaria»)를 원천 차단하기 위해, '복사만 해야
    하는' 대상 열의 토큰 내용을 숨긴다. KR 원문/EN 참조 열은 그대로 보내므로
    모델은 문장의 의미·문맥을 온전히 파악할 수 있다. «T:...» 형식 자체는
    유지되므로 기존 프롬프트 규칙과 검증 로직이 그대로 적용된다.
    응답은 unmask_placeholders() 로 복원한다.

    반환: (masked_rows, mapping)  — mapping: {"«T:1»": "«T:원문»", ...}
    번호는 배치 전체에서 고유하다 (행이 밀려도 복원 결과는 항상 원문).
    """
    mapping = {}
    counter = [0]

    def repl(m):
        counter[0] += 1
        token = f"«T:{counter[0]}»"
        mapping[token] = m.group(0)
        return token

    masked_rows = []
    for row in batch_rows:
        new_row = list(row)
        cell = row[_PH_CELL_IDX] if len(row) > _PH_CELL_IDX else None
        if isinstance(cell, str) and cell:
            new_row[_PH_CELL_IDX] = PLACEHOLDER_RE.sub(repl, cell)
        masked_rows.append(tuple(new_row))
    return masked_rows, mapping


def placeholder_legend(mapping):
    """마스킹된 «T:번호» 토큰의 실제 내용을 알려주는 참고 블록을 만든다.

    내용을 완전히 숨기면 성·수 일치, 관사/전치사 선택, 어순 판단 등 번역
    품질이 떨어질 수 있으므로, 모델에게 '내용은 참고하되 출력에는 번호
    토큰을 그대로 쓰라'고 명시한 대응표를 배치 메시지 상단에 붙인다.
    """
    if not mapping:
        return ""
    lines = [
        "[플레이스홀더 참고표 — 아래 «T:번호» 토큰의 실제 내용입니다.",
        " 의미·성수 일치·관사/전치사·어순 판단에만 참고하십시오.",
        " 출력 절대 규칙:",
        " 1) 출력에는 «T:번호» 토큰을 문자 하나 다르지 않게 원형 그대로 유지 (번호 변경 금지)",
        " 2) 토큰을 아래 내용으로 풀어 쓰거나, 내용의 일부를 « » 안에 다시 적는 것 금지",
        " 3) 출력에서 « » 기호는 입력에 있던 «T:번호» 토큰을 복사할 때만 사용 가능",
        " 4) {CL:3} 같은 {...} 코드도 원형 그대로 유지]",
    ]
    for token, original in mapping.items():
        inner = original[3:-1]  # «T:내용» → 내용
        lines.append(f"{token} = {inner}")
    return "\n".join(lines)


def unmask_placeholders(lines, mapping):
    """번역 결과 줄들의 «T:번호» 토큰을 원래 플레이스홀더로 복원한다.

    단일 패스 정규식 치환이라, 원문 플레이스홀더 안에 우연히 «T:숫자» 가
    있어도 재치환되지 않는다. 매핑에 없는 토큰(모델이 번호를 바꾼 경우)은
    그대로 두어 이후 플레이스홀더 검증에서 불일치로 드러나게 한다.
    """
    if not mapping:
        return lines

    def repl(m):
        return mapping.get(m.group(0), m.group(0))

    return [PLACEHOLDER_RE.sub(repl, ln) if ln else ln for ln in lines]


def auto_batch_count(group, start, formatter=None):
    """자동 분량 모드: 글자 수 예산에 맞춰 이번 배치에 담을 행 수를 결정한다.

    formatter 로 실제 전송 포맷(행 ID·활성 열 포함) 기준 길이를 재서,
    메시지 1건이 AUTO_BATCH_CHAR_BUDGET 글자를 넘지 않는 최대 행 수를 반환한다.
    - 짧은 행이어도 AUTO_BATCH_MAX_ROWS 를 넘지 않는다.
    - 첫 행이 혼자 예산을 초과해도 최소 1행은 보낸다 (더 쪼갤 수 없으므로).
    """
    fmt = formatter or format_batch
    try:
        budget = max(200, int(getattr(config, "AUTO_BATCH_CHAR_BUDGET", 2500)))
    except (TypeError, ValueError):
        budget = 2500
    try:
        cap = max(1, int(getattr(config, "AUTO_BATCH_MAX_ROWS", 30)))
    except (TypeError, ValueError):
        cap = 30
    n, used = 0, 0
    for row in group[start:start + cap]:
        line_len = len(fmt([row])) + 1  # 행 구분 줄바꿈 포함
        if n > 0 and used + line_len > budget:
            break
        n += 1
        used += line_len
    return max(1, n)


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
            if getattr(config, "AUTO_BATCH_SIZE", False):
                n_rows = auto_batch_count(group, bi)
            else:
                n_rows = config.BATCH_SIZE
            batch = group[bi : bi + n_rows]
            start_row_num = batch[0][0]
            end_row_num = batch[-1][0]

            if getattr(config, "AUTO_BATCH_SIZE", False):
                print(f"  배치 전송: {start_row_num}~{end_row_num}행 ({len(batch)}행, 자동 분량)")
            elif len(group) < config.BATCH_SIZE:
                print(f"  배치 전송: {start_row_num}~{end_row_num}행 ({len(batch)}행, 구멍 그룹)")
            else:
                print(f"  배치 전송: {start_row_num}~{end_row_num}행 ({len(batch)}행)")

            # 플레이스홀더 마스킹: 내부 내용을 숨긴 «T:번호» 토큰으로 전송
            if getattr(config, "MASK_PLACEHOLDERS", True):
                masked_batch, ph_map = mask_placeholders_in_batch(batch)
            else:
                masked_batch, ph_map = batch, {}

            batch_text = format_batch(masked_batch)
            if ph_map:
                batch_text = placeholder_legend(ph_map) + "\n\n" + batch_text
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
                lines = repair_masked_tokens(lines, ph_map)  # 깨진 «T:번호» 정규화
                lines = unmask_placeholders(lines, ph_map)
                if missing:
                    log_failure(missing[0], missing[-1],
                                f"응답 행 ID 누락 ({len(missing)}행)")

                # ── 한글 감지 및 재번역 ────────────────────────
                korean_idxs = filter_korean_lines(lines)
                if korean_idxs:
                    print(f"  ⚠️ 한글 감지 ({len(korean_idxs)}행) — 재번역 요청 중...")
                    retry_batch = [masked_batch[i] for i in korean_idxs if i < len(masked_batch)]
                    retry_msg = (
                        "아래 항목의 결과값에 한글이 포함되어 있습니다.\n"
                        "한글을 완전히 제거하고 목표 언어로만 재번역하여 "
                        "코드블록으로만 출력하세요.\n"
                        "각 줄 맨 앞의 R숫자 ID는 그대로 두세요.\n\n"
                        + format_batch(retry_batch)
                    )
                    if ph_map:
                        retry_msg = placeholder_legend(ph_map) + "\n\n" + retry_msg
                    send_message(driver, retry_msg)
                    wait_for_response(driver)
                    retry_response = extract_last_response(driver)
                    if retry_response:
                        # 재시도도 ID 기반 — retry_batch 순서대로 정렬된 결과를 받음
                        retry_lines, _ = parse_response(retry_response, retry_batch)
                        retry_lines = repair_masked_tokens(retry_lines, ph_map)
                        retry_lines = unmask_placeholders(retry_lines, ph_map)
                        for j, idx in enumerate(korean_idxs):
                            if j < len(retry_lines) and idx < len(lines):
                                if retry_lines[j] and not has_korean(retry_lines[j]):
                                    lines[idx] = retry_lines[j]
                                else:
                                    print(f"  ❌ {start_row_num+idx}행 재번역 후에도 한글 포함 — 원본 유지")
                        print(f"  → 재번역 완료")

                # ── 플레이스홀더 검증 → 로컬 복구 → 불일치 행 E열 표시 ──
                # 원본은 배치가 이미 들고 있는 placeholder 역할 열 값 사용
                ph_sources = batch_placeholder_sources(batch)
                ph_idxs = filter_placeholder_mismatch(ph_sources, lines)
                if ph_idxs:
                    lines, fixed = repair_placeholder_lines(ph_sources, lines, ph_idxs)
                    if fixed:
                        rows_txt = ", ".join(str(start_row_num + i) for i in fixed)
                        print(f"  🔧 플레이스홀더 로컬 복구 ({len(fixed)}행): {rows_txt}")
                    ph_idxs = filter_placeholder_mismatch(ph_sources, lines)
                if ph_idxs:
                    rows_txt = ", ".join(str(start_row_num + i) for i in ph_idxs)
                    print(f"  ⚠️ 플레이스홀더 불일치 ({len(ph_idxs)}행): {rows_txt} — E열에 표시")
                    for i in ph_idxs:
                        write_status(sheet, start_row_num + i, PH_MISMATCH_MARK)

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