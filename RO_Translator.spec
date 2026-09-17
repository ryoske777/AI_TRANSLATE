# -*- mode: python ; coding: utf-8 -*-
"""
RO_Translator.spec — PyInstaller 빌드 정의 (단일 windowed exe)

빌드:
    pip install -r requirements.txt pyinstaller
    pyinstaller RO_Translator.spec
결과:
    dist/RO_Translator.exe  (Python 미설치 PC 에서도 더블클릭 실행)

번들 포함: prompts/(기본 프롬프트), version.txt, customtkinter 데이터/테마.
번들 제외(=exe 옆 외부 파일): credentials.json, settings.json — 사용자 고유.
번들 제외(=OS 것을 쓴다): Windows 런타임(UCRT) DLL — 아래 참조.
"""

import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = [
    ("prompts", "prompts"),     # 기본 프롬프트 (시작 시 외부로 시드)
    ("version.txt", "."),       # 번들 현재 버전
]
# customtkinter 는 테마/이미지 등 데이터 파일이 함께 있어야 동작한다.
datas += collect_data_files("customtkinter")
# selenium 의 selenium-manager.exe(드라이버 자동 설치 도구)가 반드시 포함돼야
# 한다. 누락되면 exe 에서 webdriver.Chrome() 이 chromedriver 를 못 구해 실패한다.
datas += collect_data_files("selenium")

hiddenimports = []
hiddenimports += collect_submodules("customtkinter")
hiddenimports += collect_submodules("selenium")

block_cipher = None

a = Analysis(
    ["main_ui.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# ── Windows 런타임(UCRT) 번들 제외 ───────────────────────────────────────────
#
# PyInstaller 는 빌드 PC 의 파이썬 폴더 옆에 있는 ucrtbase.dll 과
# api-ms-win-*.dll(약 44개)을 '의존 DLL' 로 판단해 exe 안에 같이 담는다.
# 이 파일들은 Windows 10 부터 OS 구성요소라서 빌드 PC(여기서는 GitHub Actions
# 의 Windows Server 2025, 빌드 26100)와 사용자 PC 의 버전이 서로 다르다.
#
# onefile exe 는 실행할 때 내부 파일을 %TEMP%\_MEIxxxx 에 풀고 그 폴더를 DLL
# 검색 경로 앞쪽에 놓는다. 그래서 '빌드 PC 의 CRT' 가 시스템(System32)의 CRT
# 보다 먼저 잡히고, 어떤 모듈이 어느 쪽을 먼저 물었는지에 따라 한 프로세스
# 안에 서로 다른 빌드의 CRT 가 섞인다. 그 결과가
#     "오디널(ordinal) NNN 을(를) DLL ...\RO_Translator.exe 에서 찾을 수 없습니다"
# 같은 로더 오류이고, 로드 순서에 좌우되므로 '처음엔 뜨고 다시 실행하면 안 뜨는'
# 재현이 들쭉날쭉한 증상으로 나타난다.
#
# → OS 가 이미 갖고 있는 런타임이므로 번들에서 빼고 System32 것을 쓰게 한다.
#   (Windows 10 이상 필요. VCRUNTIME140.dll 은 OS 기본 구성요소가 아니므로 유지)
_OS_RUNTIME_PREFIXES = ("api-ms-win-", "ucrtbase")


def _is_os_runtime(dest_name):
    base = os.path.basename(dest_name).lower()
    return base.endswith(".dll") and base.startswith(_OS_RUNTIME_PREFIXES)


_dropped = [b[0] for b in a.binaries if _is_os_runtime(b[0])]
a.binaries = [b for b in a.binaries if not _is_os_runtime(b[0])]
# 빌드 로그 메시지는 반드시 ASCII 로만 쓴다. GitHub Actions 러너의 콘솔 인코딩이
# cp1252 라서 한글을 print 하면 UnicodeEncodeError 로 빌드가 통째로 죽는다.
print(f"[spec] excluded {len(_dropped)} OS runtime DLLs "
      f"(ucrtbase / api-ms-win-*) from the bundle")

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="RO_Translator",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX 압축은 쓰지 않는다. 압축된 DLL 은 로더 오류('오디널을 찾을 수 없습니다')
    # 와 백신 오탐의 단골 원인이고, 빌드 PC 에 upx 가 있느냐에 따라 결과물이
    # 달라져 재현성도 떨어진다.
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # GUI 앱 → 콘솔창 숨김
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon="app.ico",       # 아이콘 파일이 있으면 주석 해제
)
