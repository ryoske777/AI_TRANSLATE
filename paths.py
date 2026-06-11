# -*- coding: utf-8 -*-
"""
paths.py — 실행 환경(개발 .py / PyInstaller exe)에 따라 경로를 일관되게 제공.

두 종류의 경로를 구분한다.
  - app_dir():      exe(또는 스크립트) 옆의 '쓰기 가능한' 외부 폴더.
                    settings.json / credentials.json / prompts/ / 로그 등 사용자 데이터.
  - resource_dir(): 읽기 전용 '번들 리소스' 폴더.
                    exe 일 때는 PyInstaller 임시 추출 폴더(_MEIPASS),
                    개발 모드에서는 소스 폴더와 동일.

exe(--onefile)는 실행 시 내부 리소스를 임시폴더에 풀기 때문에, 사용자가 편집하거나
업데이터가 보존해야 하는 데이터는 반드시 app_dir() 쪽에 두어야 한다.
"""

import os
import sys


def is_frozen():
    """PyInstaller 등으로 묶인 exe 로 실행 중이면 True."""
    return bool(getattr(sys, "frozen", False))


def app_dir():
    """exe/스크립트 옆의 외부(쓰기 가능) 폴더."""
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def resource_dir():
    """번들 리소스(읽기 전용) 폴더. exe 면 _MEIPASS, 아니면 소스 폴더."""
    if is_frozen():
        return getattr(sys, "_MEIPASS", app_dir())
    return os.path.dirname(os.path.abspath(__file__))


def app_path(*parts):
    """app_dir() 기준 절대경로."""
    return os.path.join(app_dir(), *parts)


def resource_path(*parts):
    """resource_dir() 기준 절대경로."""
    return os.path.join(resource_dir(), *parts)
