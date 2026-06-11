# -*- coding: utf-8 -*-
"""
updater.py — GitHub 공개 repo 기반 자동 업데이트 엔진

동작 개요
  1) GitHub raw 에서 version.json 을 받아 원격 버전/파일 해시 목록 확인
  2) 로컬 파일 해시 및 .update_manifest.json(직전 배포 해시 기록)과 3-way 비교
       - 사용자가 편집하지 않은 파일만 갱신 (prompts 보호)
       - credentials.json / settings.json / manifest 자체는 항상 제외
  3) 변경된 파일만 다운로드하여 교체
  4) 갱신된 파일 목록/버전을 .update_manifest.json 에 기록

이 모듈은 UI(main_ui.py)에서 호출한다. 네트워크/파일 작업만 담당하고,
사용자 확인 대화상자나 재시작은 호출 측(UI)이 처리한다.
"""

import os
import json
import hashlib
import urllib.request
import urllib.error

# ── 설정 — 본인 GitHub 정보로 변경 ───────────────────────────────────────────
#   예: https://github.com/<USER>/<REPO> (공개 repo)
#   브랜치는 보통 main. 파일은 repo 루트 기준 경로로 version.json 에 기록한다.
GITHUB_USER   = "ryoske777"     # ← 본인 GitHub 아이디
GITHUB_REPO   = "ai_translate"  # ← repo 이름
GITHUB_BRANCH = "main"

# raw 콘텐츠 베이스 URL (공개 repo는 인증 불필요)
RAW_BASE = f"https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_REPO}/{GITHUB_BRANCH}"

# ── 업데이트에서 항상 제외할 파일 (사용자 고유/민감 데이터) ───────────────────
HARD_EXCLUDE = {
    "credentials.json",       # 사용자별 구글 서비스 계정 키 — 절대 건드리지 않음
    "settings.json",          # 사용자 설정 — 보존
    ".update_manifest.json",  # 업데이트 기록 자체
}

# 로컬 작업 폴더 (이 파일이 있는 위치 기준)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MANIFEST_PATH = os.path.join(BASE_DIR, ".update_manifest.json")

# 현재 설치된 버전 (version.py 가 있으면 거기서, 없으면 manifest, 둘 다 없으면 0.0.0)
def get_local_version():
    # 1) version.txt 우선
    vpath = os.path.join(BASE_DIR, "version.txt")
    if os.path.exists(vpath):
        try:
            with open(vpath, "r", encoding="utf-8") as f:
                v = f.read().strip()
                if v:
                    return v
        except Exception:
            pass
    # 2) manifest 기록
    mani = _load_manifest()
    return mani.get("version", "0.0.0")


# ── 유틸 ─────────────────────────────────────────────────────────────────────

def _sha256(path):
    """파일의 sha256 해시. 없으면 None."""
    if not os.path.exists(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_manifest():
    if not os.path.exists(MANIFEST_PATH):
        return {}
    try:
        with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_manifest(version, file_hashes):
    data = {"version": version, "files": file_hashes}
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _version_tuple(v):
    """'1.10.2' → (1,10,2). 비교용. 숫자 아닌 부분은 0 취급."""
    parts = []
    for p in str(v).split("."):
        num = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(num) if num else 0)
    return tuple(parts)


def is_newer(remote, local):
    """remote 버전이 local 보다 높으면 True"""
    return _version_tuple(remote) > _version_tuple(local)


def _http_get(url, timeout=15):
    """URL 본문을 bytes 로 반환. 실패 시 예외."""
    req = urllib.request.Request(url, headers={"User-Agent": "RO-LocTool-Updater"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


# ── 1단계: 원격 version.json 확인 ────────────────────────────────────────────

def fetch_remote_manifest(timeout=15):
    """GitHub 에서 version.json 을 받아 dict 로 반환.

    반환 형식:
      {"version": "1.3.0", "files": {"main.py": "<sha256>", "prompts/es.txt": "...", ...}}
    실패 시 None.
    """
    url = f"{RAW_BASE}/version.json"
    try:
        raw = _http_get(url, timeout=timeout)
        data = json.loads(raw.decode("utf-8"))
        if "files" not in data or "version" not in data:
            return None
        return data
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, TimeoutError):
        return None
    except Exception:
        return None


# ── 2단계: 무엇을 받을지 계획 (3-way 비교) ───────────────────────────────────

def plan_update(remote):
    """원격 manifest 와 로컬 상태를 비교해 업데이트 계획을 만든다.

    반환:
      {
        "to_update": [상대경로, ...],   # 받아서 덮을 파일
        "skipped":   [(상대경로, 사유), ...],  # 사용자 편집 등으로 보존
      }
    """
    remote_files = remote.get("files", {})
    mani = _load_manifest()
    mani_files = mani.get("files", {})

    to_update = []
    skipped = []

    for rel_path, remote_hash in remote_files.items():
        # 정규화 + 하드 제외
        rel_norm = rel_path.replace("\\", "/")
        base_name = os.path.basename(rel_norm)
        if rel_norm in HARD_EXCLUDE or base_name in HARD_EXCLUDE:
            continue

        local_path = os.path.join(BASE_DIR, *rel_norm.split("/"))
        local_hash = _sha256(local_path)

        # (a) 로컬에 이미 최신 → 스킵
        if local_hash == remote_hash:
            continue

        # (b) 로컬에 파일 없음 → 새 파일, 받음
        if local_hash is None:
            to_update.append(rel_norm)
            continue

        # (c) 로컬 파일 있고 원격과 다름 → 편집 여부 판정
        recorded = mani_files.get(rel_norm)
        if recorded is not None and recorded == local_hash:
            # 직전 배포본 그대로 = 사용자가 안 건드림 → 갱신
            to_update.append(rel_norm)
        elif recorded is None:
            # manifest 기록이 없음(과거 수동 설치 등).
            # 코어 코드(.py)는 갱신, prompts 등 데이터는 보존(안전 우선).
            if rel_norm.endswith(".py") or base_name.endswith(".bat"):
                to_update.append(rel_norm)
            else:
                skipped.append((rel_norm, "기록 없음 — 사용자 데이터일 수 있어 보존"))
        else:
            # 기록과 로컬이 다름 = 사용자가 편집함 → 보존
            skipped.append((rel_norm, "사용자가 편집한 파일 — 보존"))

    return {"to_update": to_update, "skipped": skipped}


# ── 3단계: 실제 다운로드·교체 ────────────────────────────────────────────────

def apply_update(remote, plan, log=None):
    """plan["to_update"] 의 파일을 받아 교체하고 manifest 를 갱신한다.

    log: 진행 메시지를 받을 콜백 (없으면 print). 반환: (성공수, 실패목록)
    실행 중인 .py 도 그냥 덮는다(파일 교체는 가능). 적용은 재시작 후.
    """
    def _log(msg):
        (log or print)(msg)

    remote_files = remote.get("files", {})
    ok, failed = 0, []

    # 다운로드는 임시파일 → 성공 시 교체 (중간 실패로 깨진 파일 방지)
    for rel in plan["to_update"]:
        url = f"{RAW_BASE}/{rel}"
        dest = os.path.join(BASE_DIR, *rel.split("/"))
        tmp = dest + ".part"
        try:
            data = _http_get(url)
            os.makedirs(os.path.dirname(dest) or BASE_DIR, exist_ok=True)
            with open(tmp, "wb") as f:
                f.write(data)
            # 받은 파일 해시가 원격 기대값과 일치하는지 검증
            got_hash = hashlib.sha256(data).hexdigest()
            expect = remote_files.get(rel)
            if expect and got_hash != expect:
                os.remove(tmp)
                failed.append((rel, "해시 불일치 — 손상 가능"))
                _log(f"  ✗ {rel} (해시 불일치)")
                continue
            os.replace(tmp, dest)  # 원자적 교체
            ok += 1
            _log(f"  ✓ {rel}")
        except Exception as e:
            if os.path.exists(tmp):
                try: os.remove(tmp)
                except Exception: pass
            failed.append((rel, str(e)))
            _log(f"  ✗ {rel} ({e})")

    # manifest 갱신: 이번에 받은 것 + 원래 최신이던 것 = 현재 로컬의 '원본 해시' 기록
    # (편집 보존된 파일은 원격 해시로 기록하지 않고, 현재 로컬 해시를 남겨 다음 비교 정확도 유지)
    new_hashes = {}
    for rel, rhash in remote_files.items():
        rel_norm = rel.replace("\\", "/")
        base_name = os.path.basename(rel_norm)
        if rel_norm in HARD_EXCLUDE or base_name in HARD_EXCLUDE:
            continue
        local_path = os.path.join(BASE_DIR, *rel_norm.split("/"))
        lh = _sha256(local_path)
        # 갱신 성공해 원격과 같아졌으면 원격 해시, 아니면(보존됨) 현재 로컬 해시
        new_hashes[rel_norm] = lh if lh else rhash

    _save_manifest(remote.get("version", "0.0.0"), new_hashes)
    return ok, failed


# ── 통합 진입점 ──────────────────────────────────────────────────────────────

def check_for_update(timeout=15):
    """업데이트 가능 여부만 빠르게 확인. UI 시작 시 호출.

    반환:
      None                         네트워크 실패 또는 version.json 없음
      {"available": False, ...}    이미 최신
      {"available": True, "version": "x", "local": "y",
       "plan": {...}, "remote": {...}}   업데이트 있음
    """
    remote = fetch_remote_manifest(timeout=timeout)
    if remote is None:
        return None
    local_v = get_local_version()
    remote_v = remote.get("version", "0.0.0")

    plan = plan_update(remote)
    has_files = bool(plan["to_update"])

    available = is_newer(remote_v, local_v) and has_files
    return {
        "available": available,
        "version": remote_v,
        "local": local_v,
        "plan": plan,
        "remote": remote,
    }
