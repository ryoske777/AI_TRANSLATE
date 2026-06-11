# -*- coding: utf-8 -*-
"""
updater.py — GitHub Releases 기반 자동 업데이트 엔진 (EXE 배포용)

동작 개요
  1) GitHub Releases API 에서 최신 릴리스(tag + .exe 자산)를 확인
  2) 태그 버전이 현재 번들 버전(version.txt)보다 높으면 업데이트 있음
  3) 새 .exe 를 내려받아 exe 옆에 저장
  4) 실행 중인 exe 는 잠겨 있으므로, 종료를 기다렸다 교체·재실행하는
     배치 스크립트를 분리 프로세스로 띄우고 본 프로세스는 종료

prompts 등 사용자 데이터는 exe 밖(app_dir)에 있고, 새 exe 안의 기본 프롬프트는
main.ensure_external_prompts() 가 시작 시 3-way 로 머지(편집 보존)한다.
따라서 업데이트는 'exe 통째 교체' 한 가지만 책임진다.

이 모듈은 UI(main_ui.py)에서 호출한다. 네트워크/파일 작업만 담당하며,
사용자 확인 대화상자는 호출 측(UI)이 처리한다.
"""

import os
import sys
import time
import json
import shutil
import subprocess
import urllib.request
import urllib.error

import paths

# ── 설정 — 공개 repo 정보 ────────────────────────────────────────────────────
GITHUB_USER = "ryoske777"
GITHUB_REPO = "ai_translate"
API_LATEST  = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/releases/latest"
ASSET_SUFFIX = ".exe"   # 릴리스에서 찾을 자산 확장자

_UA = "RO-LocTool-Updater"


def can_self_update():
    """exe(frozen) 로 실행 중일 때만 자동 교체가 가능하다."""
    return paths.is_frozen()


# ── 버전 ─────────────────────────────────────────────────────────────────────

def get_local_version():
    """현재 설치 버전. 번들 version.txt 우선, 없으면 외부 폴더."""
    for base in (paths.resource_dir(), paths.app_dir()):
        p = os.path.join(base, "version.txt")
        try:
            with open(p, "r", encoding="utf-8") as f:
                v = f.read().strip()
                if v:
                    return v
        except Exception:
            pass
    return "0.0.0"


def _version_tuple(v):
    """'1.10.2' → (1,10,2). 숫자 아닌 부분은 0 취급. 비교용."""
    parts = []
    for p in str(v).split("."):
        num = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(num) if num else 0)
    return tuple(parts)


def is_newer(remote, local):
    """remote 버전이 local 보다 높으면 True."""
    return _version_tuple(remote) > _version_tuple(local)


# ── HTTP ─────────────────────────────────────────────────────────────────────

def _http_get(url, timeout=15):
    req = urllib.request.Request(
        url, headers={"User-Agent": _UA, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _http_download(url, dest, timeout=120, progress=None):
    """url 을 dest 로 스트리밍 저장. progress(done, total) 콜백 선택."""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        with open(dest, "wb") as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress:
                    try:
                        progress(done, total)
                    except Exception:
                        pass


# ── 릴리스 확인 ──────────────────────────────────────────────────────────────

def fetch_latest_release(timeout=15):
    """최신 릴리스 정보를 dict 로 반환. 실패 시 None.

    반환: {"tag", "version", "url"(.exe 다운로드), "size", "notes"}
    """
    try:
        raw = _http_get(API_LATEST, timeout=timeout)
        data = json.loads(raw.decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, TimeoutError):
        return None
    except Exception:
        return None

    tag = (data.get("tag_name") or "").strip()
    exe = None
    for a in (data.get("assets") or []):
        if str(a.get("name", "")).lower().endswith(ASSET_SUFFIX):
            exe = a
            break
    return {
        "tag":     tag,
        "version": tag.lstrip("vV") or "0.0.0",
        "url":     exe.get("browser_download_url") if exe else None,
        "size":    exe.get("size", 0) if exe else 0,
        "notes":   (data.get("body") or "").strip(),
    }


def check_for_update(timeout=15):
    """업데이트 가능 여부 확인. UI 시작/수동확인에서 호출.

    반환:
      None                          네트워크 실패 또는 릴리스 없음
      {"available": bool, "version", "local", "url", "size",
       "notes", "self_update"}      self_update=False 면 개발(.py) 모드
    """
    rel = fetch_latest_release(timeout=timeout)
    if rel is None:
        return None
    local = get_local_version()
    remote = rel["version"]
    available = bool(rel["url"]) and is_newer(remote, local)
    return {
        "available":   available,
        "version":     remote,
        "local":       local,
        "url":         rel["url"],
        "size":        rel["size"],
        "notes":       rel["notes"],
        "self_update": can_self_update(),
    }


# ── 다운로드 ─────────────────────────────────────────────────────────────────

def download_update(info, progress=None, timeout=180):
    """새 exe 를 받아 exe 옆에 저장하고 그 경로를 반환. 실패 시 예외."""
    url = info.get("url")
    if not url:
        raise RuntimeError("릴리스에 .exe 자산이 없습니다.")
    dest = paths.app_path("_update_download.exe")
    tmp = dest + ".part"
    try:
        _http_download(url, tmp, timeout=timeout, progress=progress)
        os.replace(tmp, dest)
    except Exception:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass
        raise
    return dest


# ── 교체·재실행 ──────────────────────────────────────────────────────────────
#
# 교체는 '새 exe 가 직접 수행'한다. 배치 스크립트의 cmd /c 인자 따옴표 버그와
# 한글/공백 경로 인코딩 문제를 피하기 위해, 다운로드한 새 exe 를
#   new.exe --apply-update "<구 exe 경로>"
# 로 띄운다. 새 exe 는 구 프로세스가 종료되길 기다렸다가 자신을 구 경로에
# 덮어쓰고(=교체) 구 경로를 재실행한다. 모든 파일 작업은 파이썬이 처리한다.

APPLY_FLAG = "--apply-update"

_DETACHED_PROCESS         = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200


def apply_and_restart(new_exe):
    """다운로드한 새 exe 에게 교체를 위임하고, 호출 측은 즉시 종료해야 한다.

    현재(구) exe 는 실행 중이라 잠겨 있으므로 직접 덮을 수 없다. 대신 새 exe 를
    --apply-update 모드로 띄우고, 본 프로세스는 곧바로(os._exit) 종료해 잠금을 푼다.
    """
    if not paths.is_frozen():
        raise RuntimeError("개발 모드에서는 자동 교체를 지원하지 않습니다. git pull 로 갱신하세요.")
    cur = os.path.abspath(sys.executable)
    new = os.path.abspath(new_exe)
    subprocess.Popen(
        [new, APPLY_FLAG, cur],
        creationflags=_DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )


def perform_swap(target):
    """--apply-update 모드 진입점: 이 새 exe 를 target(구 exe)에 덮어쓰고 재실행.

    구 프로세스가 종료돼 target 잠금이 풀릴 때까지 폴링하며 교체를 시도한다.
    GUI 없이 동작하며, 끝나면 target 을 실행하고 종료한다.
    """
    src = os.path.abspath(sys.executable)
    target = os.path.abspath(target)
    deadline = time.time() + 60
    swapped = False
    while time.time() < deadline:
        try:
            shutil.copy2(src, target)   # 구 exe 가 잠겨 있으면 예외 → 재시도
            swapped = True
            break
        except Exception:
            time.sleep(0.5)
    # 교체 성공/실패와 무관하게 target 을 재실행한다(실패 시 구버전 유지).
    try:
        subprocess.Popen(
            [target],
            creationflags=_DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )
    except Exception:
        pass
    return swapped


def cleanup_after_update():
    """업데이트 직후 첫 실행 시 임시 다운로드 파일을 정리한다(있으면)."""
    for name in ("_update_download.exe", "_apply_update.bat"):
        p = paths.app_path(name)
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass
