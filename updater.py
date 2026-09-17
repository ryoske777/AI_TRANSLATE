# -*- coding: utf-8 -*-
"""
updater.py — GitHub Releases 기반 자동 업데이트 엔진 (EXE 배포용)

동작 개요
  1) GitHub Releases API 에서 최신 릴리스(tag + .exe 자산)를 확인
  2) 태그 버전이 현재 번들 버전(version.txt)보다 높으면 업데이트 있음
  3) 새 .exe 를 내려받아 exe 옆에 저장 (크기·PE 서명 검증)
  4) 실행 중인 exe 는 잠겨 있으므로, 새 exe 를 --apply-update 모드로 띄워
     구 프로세스 종료를 기다렸다 교체·재실행하게 하고 본 프로세스는 종료

prompts 등 사용자 데이터는 exe 밖(app_dir)에 있고, 새 exe 안의 기본 프롬프트는
main.ensure_external_prompts() 가 시작 시 3-way 로 머지(편집 보존)한다.
따라서 업데이트는 'exe 통째 교체' 한 가지만 책임진다.

이 모듈은 UI(main_ui.py)에서 호출한다. 네트워크/파일 작업만 담당하며,
사용자 확인 대화상자는 호출 측(UI)이 처리한다.
"""

import os
import sys
import glob
import stat
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
    """url 을 dest 로 스트리밍 저장. progress(done, total) 콜백 선택.

    반환: (받은 바이트 수, 서버가 알려준 전체 크기) — 중간에 끊겼는지 확인용.
    """
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
            f.flush()
            os.fsync(f.fileno())
    return done, total


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

DL_BASENAME = "_update_download"


def _make_writable(path):
    """읽기 전용 속성 때문에 지워지지 않는 경우를 푼다."""
    try:
        os.chmod(path, stat.S_IWRITE)
    except Exception:
        pass


def _clear_path(path):
    """그 경로를 쓸 수 있게 만든다. 없거나 지웠으면 True, 잠겨 있으면 False."""
    if not os.path.exists(path):
        return True
    _make_writable(path)
    try:
        os.remove(path)
        return True
    except Exception:
        return False


def _pick_download_path():
    """다운로드 결과를 둘 경로를 고른다.

    기본 이름(_update_download.exe)이 잠겨 있으면(백신 검사 중이거나 지난 번
    찌꺼기가 물려 있으면) 번호를 붙인 다른 이름을 쓴다. '이름 하나가 막혔다'는
    이유로 업데이트 전체가 실패하지 않게 하기 위함이다.
    """
    base = paths.app_path(DL_BASENAME + ".exe")
    if _clear_path(base):
        return base
    for i in range(2, 10):
        alt = paths.app_path(f"{DL_BASENAME}_{i}.exe")
        if _clear_path(alt):
            return alt
    return paths.app_path(f"{DL_BASENAME}_{os.getpid()}.exe")


def _finalize_download(tmp, dest):
    """검증까지 끝난 .part 를 최종 이름으로 옮긴다. 옮긴 경로를 반환.

    Windows 에서 이 rename 이 [WinError 5] 로 막히는 경우가 실제로 있다.
      · 방금 받은 exe 를 백신이 실시간 검사 중이라 잠깐 잠겨 있다
        (특히 다운로드 폴더·바탕화면에서 자주 걸린다)
      · 지난 번 업데이트가 남긴 _update_download.exe 가 아직 물려 있다
    둘 다 '잠깐' 이거나 '이름만 바꾸면 되는' 문제라, 재시도 → 다른 이름 →
    받은 파일 그대로 쓰기 순으로 물러선다. 파일 내용은 이미 검증됐으므로
    이름이 무엇이든 교체에 쓰는 데는 문제가 없다.
    """
    deadline = time.time() + 20
    delay = 0.3
    last = None
    while time.time() < deadline:
        try:
            os.replace(tmp, dest)
            return dest
        except OSError as e:
            last = e
            _make_writable(dest)
            time.sleep(delay)
            delay = min(delay * 1.6, 2.0)
    _log(f"다운로드 파일 이름 변경 재시도 실패: {last!r}")

    alt = paths.app_path(f"{DL_BASENAME}_{os.getpid()}.exe")
    try:
        os.replace(tmp, alt)
        _log(f"대체 이름으로 저장: {alt}")
        return alt
    except OSError as e:
        _log(f"대체 이름도 실패({e!r}) — 받은 파일을 그대로 사용한다: {tmp}")
        return tmp          # 검증을 통과한 파일이므로 그대로 써도 안전하다


def download_update(info, progress=None, timeout=180):
    """새 exe 를 받아 exe 옆에 저장하고 그 경로를 반환. 실패 시 예외.

    받다 만 파일로 교체해 버리면 exe 자체가 깨져 '실행이 안 되는' 상태가 되므로,
    크기(Content-Length / 릴리스 자산 크기)와 PE 서명(MZ)까지 확인한 뒤에만
    최종 이름으로 옮긴다.
    """
    url = info.get("url")
    if not url:
        raise RuntimeError("릴리스에 .exe 자산이 없습니다.")
    dest = _pick_download_path()
    tmp = dest + ".part"
    _clear_path(tmp)
    try:
        got, expected = _http_download(url, tmp, timeout=timeout, progress=progress)
        _verify_download(tmp, got, expected, info.get("size") or 0)
        final = _finalize_download(tmp, dest)
    except Exception:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass
        raise
    return final


def _verify_download(path, got, expected, asset_size):
    """받은 파일이 '온전한 Windows 실행 파일' 인지 확인. 아니면 예외."""
    size = os.path.getsize(path)
    for want, label in ((expected, "서버가 알려준 크기"), (asset_size, "릴리스 자산 크기")):
        if want and size != want:
            raise RuntimeError(
                f"다운로드가 중간에 끊겼습니다. ({label} {want:,} 바이트 / 받은 것 {size:,} 바이트)")
    if got != size:
        raise RuntimeError("다운로드한 파일 크기가 맞지 않습니다.")
    if size < 1024 * 1024:
        raise RuntimeError(f"받은 파일이 너무 작습니다. ({size:,} 바이트)")
    with open(path, "rb") as f:
        if f.read(2) != b"MZ":
            raise RuntimeError("받은 파일이 실행 파일이 아닙니다.")


# ── 교체·재실행 ──────────────────────────────────────────────────────────────
#
# 교체는 '새 exe 가 직접 수행'한다. 배치 스크립트의 cmd /c 인자 따옴표 버그와
# 한글/공백 경로 인코딩 문제를 피하기 위해, 다운로드한 새 exe 를
#   new.exe --apply-update "<구 exe 경로>" [구 프로세스 PID]
# 로 띄운다. 새 exe 는 구 프로세스가 종료되길 기다렸다가 자신을 구 경로에
# 심고(=교체) 구 경로를 재실행한다. 모든 파일 작업은 파이썬이 처리한다.
#
# 교체 방법 주의 — 실행 중이던 exe 를 '덮어쓰기(copy)' 하지 않는다.
# 방금 종료한 exe 파일을 같은 자리에 그대로 덮어쓰면, Windows 가 들고 있던
# 예전 이미지 캐시와 백신 실시간 검사가 겹쳐 '교체 직후 첫 실행' 에서만
# 로더 오류가 뜨는 일이 있다. 그래서
#   ① 새 exe 를 임시 이름(_update_new.exe)으로 완전히 쓴 뒤
#   ② 구 exe 를 .old 로 밀어내고
#   ③ 임시 파일을 제 이름으로 rename (새 파일로 교체)
# 순서로 진행한다. 세 단계 모두 같은 폴더 안이라 rename 은 원자적이다.

APPLY_FLAG = "--apply-update"
BACKUP_SUFFIX = ".old"
STAGING_NAME = "_update_new.exe"

_DETACHED_PROCESS         = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200


def _log(msg):
    """교체 과정을 exe 옆 update.log 에 남긴다(문제 추적용). 실패해도 무시."""
    try:
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n"
        with open(paths.app_path("update.log"), "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def _wait_pid_exit(pid, timeout=30):
    """구 프로세스(pid)가 완전히 끝날 때까지 기다린다. Windows 전용, 실패해도 무시.

    onefile exe 는 '부트로더 프로세스 + 실제 앱 프로세스' 두 개로 돌기 때문에,
    앱이 죽어도 부트로더가 임시폴더를 지우는 동안 exe 잠금이 남아 있다.
    그래서 pid 대기 + 파일 잠금 대기를 둘 다 한다.
    """
    if not pid or os.name != "nt":
        return True
    try:
        import ctypes
        SYNCHRONIZE = 0x00100000
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(SYNCHRONIZE, False, int(pid))
        if not h:
            return True                      # 이미 종료됨
        try:
            k32.WaitForSingleObject(h, int(timeout * 1000))
        finally:
            k32.CloseHandle(h)
    except Exception:
        pass
    return True


def _wait_until_unlocked(path, timeout=60):
    """path 가 '실행 중이 아니게' 될 때까지 기다린다.

    Windows 는 실행 중인 exe 에 쓰기 핸들을 주지 않으므로, 쓰기 모드로 열리면
    그 프로세스는 완전히 종료된 것이다.
    """
    if not os.path.exists(path):
        return True                          # 교체할 파일이 아예 없다 → 기다릴 것도 없음
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with open(path, "ab"):           # 내용은 쓰지 않는다(열리는지만 확인)
                return True
        except OSError:
            time.sleep(0.3)
    return False


def apply_and_restart(new_exe):
    """다운로드한 새 exe 에게 교체를 위임하고, 호출 측은 즉시 종료해야 한다.

    현재(구) exe 는 실행 중이라 잠겨 있으므로 직접 덮을 수 없다. 대신 새 exe 를
    --apply-update 모드로 띄우고, 본 프로세스는 곧바로(os._exit) 종료해 잠금을 푼다.
    """
    if not paths.is_frozen():
        raise RuntimeError("개발 모드에서는 자동 교체를 지원하지 않습니다. git pull 로 갱신하세요.")
    cur = os.path.abspath(sys.executable)
    new = os.path.abspath(new_exe)
    _log(f"교체 요청: {new} -> {cur} (pid {os.getpid()})")
    subprocess.Popen(
        [new, APPLY_FLAG, cur, str(os.getpid())],
        creationflags=_DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )


def perform_swap(target, old_pid=None):
    """--apply-update 모드 진입점: 이 새 exe 를 target(구 exe) 자리에 심고 재실행.

    old_pid 는 구버전(1.7.0 이하)이 넘겨주지 않으므로 없어도 동작해야 한다.
    """
    src = os.path.abspath(sys.executable)
    target = os.path.abspath(target)
    folder = os.path.dirname(target)
    staging = os.path.join(folder, STAGING_NAME)
    backup = target + BACKUP_SUFFIX
    _log(f"교체 시작: {src} -> {target} (구 pid {old_pid or '?'})")

    _wait_pid_exit(old_pid, timeout=30)
    if not _wait_until_unlocked(target, timeout=60):
        _log("경고: 구 프로세스가 60초 안에 끝나지 않았습니다. 그대로 진행합니다.")

    swapped = False
    try:
        # ① 새 exe 를 임시 이름으로 완전히 기록 (디스크까지 확실히 내려쓴다)
        _safe_remove(staging)
        shutil.copy2(src, staging)
        with open(staging, "rb+") as f:
            os.fsync(f.fileno())
        if os.path.getsize(staging) != os.path.getsize(src):
            raise RuntimeError("복사본 크기가 원본과 다릅니다.")

        # ② 구 exe 를 옆으로 밀어낸다 (실행 중이어도 이름 변경은 가능)
        _make_writable(backup)
        _safe_remove(backup)
        _make_writable(target)
        moved = False
        if os.path.exists(target):
            os.replace(target, backup)
            moved = True

        # ③ 임시 파일을 제 이름으로 — 새 파일로 교체된다
        try:
            os.replace(staging, target)
            swapped = True
        except Exception:
            if moved:
                os.replace(backup, target)   # 실패하면 구버전 원위치
            raise
        _safe_remove(backup)
        _log("교체 완료")
    except Exception as e:
        _log(f"교체 실패: {e!r}")
        _safe_remove(staging)
        if not os.path.exists(target) and os.path.exists(backup):
            try:
                os.replace(backup, target)
                _log("구버전으로 되돌렸습니다.")
            except Exception:
                pass

    # 방금 쓴 파일은 백신이 검사 중일 수 있다. 잠깐 쉬고, 읽을 수 있는지 확인한 뒤 실행.
    time.sleep(1.0)
    _wait_readable(target, timeout=15)
    try:
        subprocess.Popen(
            [target],
            creationflags=_DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )
        _log("재실행 요청 완료")
    except Exception as e:
        _log(f"재실행 실패: {e!r}")
    return swapped


def _wait_readable(path, timeout=15):
    """백신 검사 등으로 잠깐 열리지 않는 경우를 대비해 읽기 가능해질 때까지 대기."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with open(path, "rb") as f:
                if f.read(2) == b"MZ":
                    return True
        except OSError:
            pass
        time.sleep(0.3)
    return False


def _safe_remove(path):
    try:
        if os.path.exists(path):
            os.remove(path)
            return True
    except Exception:
        pass
    return False


def cleanup_after_update():
    """업데이트 직후 첫 실행 시 남은 임시 파일을 정리한다(있으면).

    이름이 하나가 아니다 — 기본 이름이 잠겨 있으면 번호/PID 를 붙인 이름으로
    받기 때문에(_pick_download_path) 같은 계열을 전부 훑어 지운다.
    """
    names = ["_apply_update.bat", STAGING_NAME]
    if paths.is_frozen():
        names.append(os.path.basename(sys.executable) + BACKUP_SUFFIX)
    for name in names:
        _safe_remove(paths.app_path(name))
    for pattern in (DL_BASENAME + "*.exe", DL_BASENAME + "*.part"):
        for path in glob.glob(paths.app_path(pattern)):
            _make_writable(path)
            _safe_remove(path)
