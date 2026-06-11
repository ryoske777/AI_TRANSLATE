# -*- coding: utf-8 -*-
"""
make_version.py — 배포자(개발자)용 도구

사용법:
    python make_version.py 1.3.0
    python make_version.py            (버전 미지정 시 version.txt 값 + 패치 자동 증가)

동작:
    - 배포 대상 파일들의 sha256 해시를 계산해 version.json 생성
    - version.txt 도 함께 갱신
    - 생성된 version.json / version.txt / 코드 파일을 GitHub 에 push 하면 끝

배포 대상: 아래 INCLUDE 패턴에 맞는 파일.
제외: credentials.json, settings.json 등 사용자 고유 파일은 절대 포함하지 않는다.
"""

import os
import sys
import json
import hashlib
import fnmatch

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ── 배포에 포함할 파일 패턴 ──────────────────────────────────────────────────
INCLUDE_PATTERNS = [
    "*.py",            # 코어 코드 (단, 아래 EXCLUDE 로 일부 제외)
    "*.bat",           # 실행 스크립트
    "prompts/*.txt",   # 프롬프트 (사용자 편집분은 updater 가 보존)
    "config.py",
]

# ── 절대 배포하지 않을 파일 ──────────────────────────────────────────────────
EXCLUDE_NAMES = {
    "credentials.json",
    "settings.json",
    ".update_manifest.json",
    "make_version.py",   # 배포자 전용 도구는 사용자에게 안 보냄
    "version.json",      # 생성물 자체
}
EXCLUDE_PATTERNS = [
    "*.part",            # 다운로드 임시파일
    "*.pyc",
    "test_*.py",
    "__pycache__/*",
]


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def matched(rel):
    """rel 경로가 INCLUDE 에 맞고 EXCLUDE 에 안 걸리면 True"""
    base = os.path.basename(rel)
    if base in EXCLUDE_NAMES:
        return False
    for pat in EXCLUDE_PATTERNS:
        if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(base, pat):
            return False
    for pat in INCLUDE_PATTERNS:
        if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(base, pat):
            return True
    return False


def collect_files():
    files = {}
    for root, dirs, names in os.walk(BASE_DIR):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", "chrome-session", ".git")]
        for name in names:
            full = os.path.join(root, name)
            rel = os.path.relpath(full, BASE_DIR).replace("\\", "/")
            if matched(rel):
                files[rel] = sha256(full)
    return files


def read_version_txt():
    p = os.path.join(BASE_DIR, "version.txt")
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            return f.read().strip()
    return "0.0.0"


def bump_patch(v):
    parts = v.split(".")
    while len(parts) < 3:
        parts.append("0")
    try:
        parts[-1] = str(int(parts[-1]) + 1)
    except ValueError:
        parts[-1] = "1"
    return ".".join(parts)


def main():
    if len(sys.argv) >= 2:
        version = sys.argv[1].strip()
    else:
        version = bump_patch(read_version_txt())
        print(f"버전 미지정 → 자동 증가: {version}")

    files = collect_files()
    if not files:
        print("⚠️  배포 대상 파일을 찾지 못했습니다. INCLUDE 패턴을 확인하세요.")
        sys.exit(1)

    manifest = {"version": version, "files": files}
    with open(os.path.join(BASE_DIR, "version.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    with open(os.path.join(BASE_DIR, "version.txt"), "w", encoding="utf-8") as f:
        f.write(version)

    print(f"\n✅ version.json 생성 완료 (v{version}, 파일 {len(files)}개)")
    print("─" * 50)
    for rel in sorted(files):
        print(f"  {rel}")
    print("─" * 50)
    print("\n다음 단계:")
    print("  1) git add -A")
    print(f'  2) git commit -m "release v{version}"')
    print("  3) git push")
    print("\n사용자는 다음 실행 때 자동으로 업데이트를 안내받습니다.")


if __name__ == "__main__":
    main()
