# -*- coding: utf-8 -*-
"""
make_prompt_baseline.py — 배포자용. 과거 모든 버전의 기본 프롬프트 해시를 모은다.

왜 필요한가:
  ensure_external_prompts() 는 사용자가 편집한 프롬프트를 보존하려고 '직전 시드본
  해시'와 비교한다. 그런데 시드 기록 장치(.prompt_seed.json)가 생기기 전에 깔린
  파일은 기록이 없어서, 편집하지 않았는데도 '편집했을지 모른다'며 영원히 보존됐다.
  → 프롬프트를 고쳐 배포해도 그 사용자에게는 전달되지 않는 침묵 버그.

해결:
  git 이력에 있는 '모든 과거 기본값'의 해시를 미리 모아 둔다. 사용자 파일이 그중
  하나와 같으면 '한 번도 편집하지 않은 옛 기본값' 이 확실하므로 안심하고 갱신한다.
  어느 것과도 다르면 진짜 사용자 편집이므로 그대로 보존한다.

사용법:
    python tools/make_prompt_baseline.py
    → prompts/_known_defaults.json 생성 (커밋할 것)
"""

import hashlib
import json
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "prompts", "_known_defaults.json")


def git(*args):
    return subprocess.run(["git", "-C", BASE, *args],
                          capture_output=True, check=True).stdout


def main():
    commits = git("log", "--format=%H").decode().split()
    known = {}
    for commit in commits:
        try:
            names = git("ls-tree", "--name-only", f"{commit}:prompts").decode().split("\n")
        except subprocess.CalledProcessError:
            continue
        for name in names:
            name = name.strip()
            if not name.endswith(".txt"):
                continue
            try:
                blob = git("show", f"{commit}:prompts/{name}")
            except subprocess.CalledProcessError:
                continue
            known.setdefault(name, set()).add(hashlib.sha256(blob).hexdigest())

    data = {name: sorted(hashes) for name, hashes in sorted(known.items())}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    total = sum(len(v) for v in data.values())
    print(f"[OK] {OUT}")
    print(f"     프롬프트 {len(data)}종 · 과거 기본값 해시 {total}개")
    for name, hashes in data.items():
        print(f"       {name:22s} {len(hashes)}개 버전")
    return 0


if __name__ == "__main__":
    sys.exit(main())
