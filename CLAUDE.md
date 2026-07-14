# AI_TRANSLATE — 작업 규칙

## 릴리스 규칙 (필수)

**코드를 변경하는 모든 작업은 반드시 버전업을 함께 포함한다.**

- `version.txt` 의 버전을 올린다 (보통 패치 +1, 예: 1.4.9 → 1.4.10).
- 이 툴의 자동 업데이트는 "GitHub 최신 릴리스 태그 버전 > 툴에 내장된
  version.txt" 일 때만 동작한다 (updater.py 참조). 버전업 없이 main에
  머지되면 GitHub Actions가 **기존 태그 릴리스에 exe만 덮어써서** 사용자에게
  업데이트 안내가 영영 뜨지 않는다.
- 커밋 메시지는 기존 관례를 따른다: `release v1.4.9: <변경 요약>`
  (코드 수정과 버전업을 같은 브랜치에 담아 한 번의 머지로 릴리스되게 한다.)
- main 머지 → `.github/workflows/release.yml` 이 자동으로 exe 빌드 후
  `v{version.txt}` 태그로 릴리스에 첨부한다.

## 구조 메모

- 실사용 진입점은 `main_ui.py` (customtkinter UI). `main.py` 는 엔진 +
  CLI. 상세 구조는 `HANDOFF.md` 참조.
- 검증·복구 로직(플레이스홀더 `«T:...»`, 엔진 코드 `{CL:n}`)은 `main.py` 가
  단일 소스이고 `main_ui.py` 가 import 해서 쓴다 — 양쪽에 중복 구현 금지.
- 프롬프트 파일(`prompts/*.txt`)은 사용자가 자유 편집하므로, 시스템이
  반드시 보장해야 하는 규칙(행 ID, 토큰 보존)은 파일이 아니라 코드가
  주입한다 (`ID_RULE_BLOCK`, `PLACEHOLDER_RULE_BLOCK`).
- `settings.json` / `credentials.json` 은 사용자 고유 파일 — repo에 커밋 금지.
