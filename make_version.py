# -*- coding: utf-8 -*-
"""
make_version.py — 배포자(개발자)용 릴리스 도구 (EXE 배포)

사용법:
    python make_version.py 1.0.1     # 버전 지정
    python make_version.py           # version.txt 패치 자동 증가

동작:
    - version.txt 를 새 버전으로 기록 (이 값이 exe 에 번들되어 '현재 버전'이 됨)
    - 커밋 + push 안내 출력

배포 흐름(요약):
    1) python make_version.py 1.0.1
    2) git add -A && git commit -m "release v1.0.1"
    3) git push origin main
    -> GitHub Actions(.github/workflows/release.yml)가 main 푸시를 감지해 Windows 에서
       exe 를 빌드하고 v1.0.1 릴리스에 RO_Translator.exe 를 첨부한다.
    -> 사용자는 다음 실행 때 자동으로 업데이트를 안내받는다.

해시 매니페스트(version.json)는 더 이상 쓰지 않는다. 업데이트는 GitHub
Releases 의 태그 + exe 자산만으로 동작한다(updater.py 참조).
"""

import os
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VERSION_TXT = os.path.join(BASE_DIR, "version.txt")
BASELINE_TOOL = os.path.join(BASE_DIR, "tools", "make_prompt_baseline.py")


def read_version():
    if os.path.exists(VERSION_TXT):
        with open(VERSION_TXT, "r", encoding="utf-8") as f:
            return f.read().strip() or "0.0.0"
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
        version = sys.argv[1].strip().lstrip("vV")
    else:
        version = bump_patch(read_version())
        print(f"버전 미지정 → 자동 증가: {version}")

    with open(VERSION_TXT, "w", encoding="utf-8") as f:
        f.write(version)

    # 과거 기본 프롬프트 해시표 갱신 — 배포마다 최신이어야 한다.
    # 이게 빠지면 다음 배포에서 '직전 버전을 쓰던 사용자'의 프롬프트를 갱신하지
    # 못하고 영원히 보존해 버린다. (pt 가 pt-PT 로 계속 번역되던 사고의 원인)
    if os.path.exists(BASELINE_TOOL):
        print("\n[prompts] 과거 기본값 해시표 갱신 중...")
        try:
            subprocess.run([sys.executable, BASELINE_TOOL], check=True)
        except Exception as e:
            print(f"  [!] 해시표 갱신 실패: {e}")
            print("      tools/make_prompt_baseline.py 를 직접 실행한 뒤 커밋하세요.")

    print(f"\n[OK] version.txt -> v{version}")
    print("-" * 50)
    print("다음 단계:")
    print("  1) git add -A")
    print(f'  2) git commit -m "release v{version}"')
    print("  3) git push origin main")
    print("-" * 50)
    print("main 푸시를 GitHub Actions 가 감지해 exe 를 빌드하고 릴리스에 첨부합니다.")
    print("사용자는 다음 실행 때 자동 업데이트를 안내받습니다.")
    print("※ prompts/_known_defaults.json 도 함께 커밋되어야 합니다 (위에서 자동 갱신됨).")


if __name__ == "__main__":
    main()
