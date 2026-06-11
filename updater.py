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
import json
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

def apply_and_restart(new_exe):
    """현재 실행 중인 exe 를 new_exe 로 교체하고 재실행한다. (Windows)

    실행 중 exe 는 잠겨 있어 직접 덮을 수 없으므로, 본 프로세스가 종료될 때까지
    기다렸다 교체·재실행하는 배치 스크립트를 분리 프로세스로 띄운다.
    경로(공백/한글 포함)는 인자(%1,%2)로 넘겨 bat 본문은 ASCII 만 유지한다.
    """
    if not paths.is_frozen():
        raise RuntimeError("개발 모드에서는 자동 교체를 지원하지 않습니다. git pull 로 갱신하세요.")
    cur = os.path.abspath(sys.executable)
    new = os.path.abspath(new_exe)
    bat = paths.app_path("_apply_update.bat")
    script = (
        "@echo off\r\n"
        "setlocal\r\n"
        ":retry\r\n"
        "move /y %2 %1 >nul 2>&1\r\n"
        "if errorlevel 1 (\r\n"
        "  ping -n 2 127.0.0.1 >nul\r\n"
        "  goto retry\r\n"
        ")\r\n"
        'start "" %1\r\n'
        'del "%~f0"\r\n'
    )
    with open(bat, "w", encoding="ascii") as f:
        f.write(script)

    DETACHED_PROCESS        = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    subprocess.Popen(
        ["cmd", "/c", bat, cur, new],
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )
