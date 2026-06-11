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
"""

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = [
    ("prompts", "prompts"),     # 기본 프롬프트 (시작 시 외부로 시드)
    ("version.txt", "."),       # 번들 현재 버전
]
# customtkinter 는 테마/이미지 등 데이터 파일이 함께 있어야 동작한다.
datas += collect_data_files("customtkinter")

hiddenimports = []
hiddenimports += collect_submodules("customtkinter")

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
    upx=True,
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
