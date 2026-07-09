# RO 로컬라이제이션 도구 — 작업 인수인계 (HANDOFF)

이 문서는 Claude Code에서 이어서 작업하기 위한 인수인계 문서입니다.
이전 세션(Claude 채팅)에서 진행한 내용, 구조, 결정사항, 남은 작업이 정리돼 있습니다.

---

## 0. 이 도구가 하는 일

ChatGPT(또는 Claude) 웹 UI를 Selenium으로 구동해, Google Sheets의 게임 텍스트를
다국어로 자동 번역하는 도구. 라그나로크 온라인(RO) 로컬라이제이션 업무용.

- 시트 구조: 열마다 역할(source/ref/placeholder)을 사용자가 지정. 보통 A=한글원본,
  B=영어참조, C=플레이스홀더 포함 번역대상, D=번역결과. **단 사용자마다/작업마다 다름**
  → 그래서 역할은 설정(settings.json)에서 지정하는 구조이며 코드에 하드코딩하지 않음.
- 번역 결과는 D열(RESULT_COL)에 기입.
- UI: customtkinter 기반 데스크톱 앱(main_ui.py).
- 실행: run_ui.bat → 전용 프로필 크롬을 디버깅 모드로 띄우고 pythonw main_ui.py 실행.

---

## 1. 파일 구성

### 코어 코드
- **main.py** — 번역 엔진. 시트 읽기/쓰기, 배치 포맷팅, 응답 파싱, 한글/플레이스홀더
  재시도, ChatGPT DOM 제어(send/wait/extract), 프롬프트 로딩.
- **main_ui.py** — customtkinter UI. TranslationWorker(스레드), 설정/언어/프롬프트
  다이얼로그, 자동 업데이트 통합.
- **config.py** — 설정 기본값 + 프롬프트 파일 경로. **※ 이 폴더에 없음. 사용자 기존
  작업폴더에 있으며 이번 작업에서 건드리지 않았음. Claude Code 작업 시 사용자
  폴더의 config.py를 참조할 것.**

### 프롬프트
- **prompts/** — 언어별 번역 프롬프트. 파일명은 ASCII 코드(es, en, fr, de, pt, tr,
  id, zh_cn, th, es_general). 화면 표시명은 main.py의 LANG_LABELS에서 한글로 매핑.
  - **중요**: 프롬프트 파일에는 행 ID 규칙을 넣지 않음. main.py의 ID_RULE_BLOCK이
    런타임에 자동으로 끝에 덧붙임. 프롬프트는 순수 번역 규칙만 유지.

### 자동 업데이트
- **updater.py** — GitHub 기반 업데이트 엔진. (아래 4번 섹션 참조)
- **make_version.py** — 배포자(개발자)용. version.json 자동 생성.
- **version.txt** — 현재 설치 버전 (현재 1.0.0).

### 실행/설정
- **run_ui.bat** — 실행 스크립트. (일반 크롬 안 죽이고 전용 프로필 크롬 띄움)
- **settings.json** — 사용자 설정(언어, 배치크기, 열 역할 등). **업데이트 대상 제외.**
- **credentials.json** — 구글 서비스 계정 키. **이 폴더에 없음(사용자 고유). 업데이트
  대상 제외.**

---

## 2. 이전 세션에서 완료한 작업

### (1) 행 ID 매칭 — silent corruption 방지 ✅
**문제**: 기존엔 "N행 보내면 N줄 응답" 가정으로 위치 기반 매칭. 모델이 설명 한 줄
끼우거나 순서 바꾸면 이후 전체가 밀린 채 시트에 기입되는데 경고만 뜨고 진행됨.

**해결**: 각 배치 줄 앞에 `R{행번호}\t` ID 부착(format_batch). 응답에서 ID로 되받아
batch 순서대로 정렬(parse_response). 위치가 아니라 ID로 매칭하므로 밀려도 제자리.
- `parse_response(response_text, batch_rows)` → `(lines, missing)` 반환.
  lines는 batch와 항상 같은 길이(누락은 빈칸), missing은 ID 못 찾은 행 목록.
- 호환성 유지: 반환을 list로 정렬해 기존 write_results/reconcile/재시도가 그대로 동작.
- 폴백: 모델이 ID를 통째로 무시하면 위치 기반으로 떨어짐.
- main.py / main_ui.py 양쪽 호출부 모두 반영(한글 재시도, 플레이스홀더 재시도 포함).

### (2) 언어별 프롬프트 즐겨찾기 ✅
- prompts/ 폴더 + 라디오 선택 UI. settings.json의 PROMPT_LANG에 저장.
- ID 규칙은 코드(main.py ID_RULE_BLOCK)가 주입 → 프롬프트 파일은 순수 유지.
- UI: 설정창(⚙) 안 언어 섹션 + 헤더 🌍 버튼(빠른 선택, LangDialog). 메인 화면에
  현재 언어 표시.

### (3) run_ui.bat 개선 ✅
- 기존: `taskkill /IM chrome.exe`로 일반 크롬까지 다 죽임 → 작업 중 탭 날아감.
- 개선: 포트(9222) 검사 → 열려 있으면 재사용, 없으면 전용 프로필로 새 인스턴스.
  일반 크롬 안 건드림.
- 주의: `^` 줄연속 문법 + chcp 65001 + 한글주석 조합이 환경에서 깨졌었음 →
  전부 한 줄로 합치고 ASCII 영문 주석 + CRLF로 해결.

### (4) GitHub 자동 업데이트 ✅ (이번 세션 핵심)
아래 4번 섹션 참조.

---

## 3. 진행 중 내려진 주요 결정

- **설정 단일화**: settings.json이 정본(load_settings가 config를 덮어씀). config.py는
  초기 기본값. 이건 버그가 아니라 정상 동작. 충돌 정리만 함.
- **열 역할은 하드코딩 안 함**: 사용자가 작업마다 다르게 쓰므로 UI에서 지정. (이미
  잘 된 설계, 유지)
- **ID 규칙은 코드 주입**: 프롬프트 파일에 넣지 않음. 단일 진실 원천 = main.py.
- **prompts 업데이트 정책**: "편집 안 한 것만 갱신". 해시 3-way 비교로 판별.

---

## 4. GitHub 자동 업데이트 — 구조와 설정

### 동작 흐름
```
[배포자(너)]
  코드 수정 → python make_version.py 1.x.x → version.json 생성 → git push
                                                          ↓
[사용자] 도구 켜짐 (main_ui.py __init__ 끝의 after(800, _check_update_async))
  → updater.check_for_update(): GitHub raw에서 version.json 받음
  → 로컬 버전(version.txt)보다 높고 + 받을 파일 있으면 → 팝업으로 물어봄
  → 예 → apply_update(): 변경 파일만 다운로드·교체 → 자동 재시작
```

### updater.py 필수 설정 (작업 시작 시 가장 먼저)
```python
GITHUB_USER   = "YOUR_GITHUB_ID"        # ← 실제 GitHub 아이디로 변경
GITHUB_REPO   = "ro-localization-tool"  # ← repo 이름으로 변경
GITHUB_BRANCH = "main"
```
공개 repo면 인증 불필요. raw.githubusercontent.com에서 직접 받음.

### "편집 안 한 것만 갱신" 판별 (updater.plan_update의 3-way 비교)
각 파일마다:
- 로컬 해시 == 원격 해시 → 이미 최신, 스킵
- 로컬에 파일 없음 → 새 파일, 받음
- 로컬 있고 원격과 다름 →
  - .update_manifest.json 기록 == 로컬 해시 → 사용자 안 건드림 → **갱신**
  - 기록 != 로컬 해시 → 사용자가 편집함 → **보존**
  - 기록 없음 → .py/.bat는 갱신, 데이터는 보존(안전 우선)

### 항상 제외 (HARD_EXCLUDE)
credentials.json, settings.json, .update_manifest.json — 절대 안 건드림.

### 검증 완료
편집 보존 / 안 건드린 것 갱신 / 민감파일 제외 / 다운로드·해시검증·원자적교체 /
manifest 갱신 — 가짜 환경으로 전부 테스트 통과함.

---

## 5. GitHub repo 구성 방법 (네가 할 일)

1. GitHub에 공개 repo 생성 (예: ro-localization-tool)
2. 다음 파일들을 repo 루트에 올림 (사용자 고유 파일은 제외):
   - main.py, main_ui.py, updater.py, config.py, run_ui.bat
   - prompts/ 폴더 전체
   - version.txt, version.json (make_version.py로 생성)
   - **올리면 안 되는 것**: credentials.json, settings.json, make_version.py,
     chrome-session/, .update_manifest.json
3. updater.py의 GITHUB_USER/REPO를 실제 값으로 수정 후 함께 커밋
4. 배포 시: `python make_version.py 1.x.x` → `git add -A` → `git commit` → `git push`

`.gitignore` 권장 내용:
```
credentials.json
settings.json
.update_manifest.json
chrome-session/
__pycache__/
*.pyc
*.part
make_version.py
```
(make_version.py는 배포자 전용이라 repo에 둬도 되지만, 사용자 자동업데이트
대상에선 make_version.py의 EXCLUDE로 이미 빠짐)

---

## 6. 남은 작업 / 개선 후보 (선택)

이전 세션에서 식별했으나 아직 안 한 것들:

- **엔진 단일화**: main.py와 main_ui.py에 번역 루프가 중복 존재. 콜백 기반으로
  추상화해 한 벌로 만들면 유지보수 쉬워짐. (현재는 양쪽 다 ID매칭 반영해둠)
- **claude -p 서브프로세스 백엔드**: Selenium DOM 의존(OpenAI UI 셀렉터)이 깨지기
  쉬움. Anthropic ToS상 Selenium 자동화 이슈도 있어 `claude -p` CLI 백엔드로
  전환 검토했던 사안. (config.AI_MODE에 "claude" 옵션 존재, claude_driver.py 연동)
- **셀렉터 중앙화**: prompt-textarea, send-button 등 ChatGPT DOM 셀렉터가 코드에
  흩어져 있음. 한 곳(dict)으로 모으면 UI 변경 대응 쉬움.
- **col_to_idx 26열 한계**: AA, AB 등 못 다룸. RESULT_COL이 D 고정이라 당장 문제
  없으나 기록.

---

## 7. 작업 모드 — 번역 / 검수 (2026-07 추가)

메인 화면 상단 세그먼트 버튼으로 3가지 모드를 전환한다 (config.WORK_MODE).

- **번역(translate)**: 기존 동작 그대로.
- **용어집 검수(review_glossary)**: 단어+카테고리 시트. 동음이의어(카테고리 문맥),
  지역 변형(예: 유럽 스페인어 es-ES), 게임 용어로서의 자연스러움을 검수.
- **일반 검수(review_general)**: 문장 시트. 원문→대상 언어 번역 품질(정확성/자연스러움/
  지역 기준/형식 보존)을 검수.

구조:
- **열 역할은 모드별 프리셋** (config.MODE_COL_ROLES, settings.json 저장).
  모드 전환 시 apply_mode_columns()가 COL_*_ROLE/RESULT_COL 에 적용. 검수 모드엔
  새 역할 `review`(검수대상, 필수)와 `category`(용어집 검수용)가 있음.
- **검수 언어쌍**은 메인 화면 드롭다운 (REVIEW_SRC_LANG → REVIEW_TGT_LANG, ko 포함).
- **프롬프트 템플릿**: prompts/review_glossary.txt, prompts/review_general.txt.
  {SRC_LANG}/{TGT_LANG} 토큰이 실행 시 REVIEW_LANG_DESC(지역 변형 명시)로 치환되고,
  검수용 행 ID 규칙(REVIEW_ID_RULE_BLOCK)이 자동 주입됨 (main.load_review_prompt).
  이 파일들은 list_prompt_langs() 언어 목록에서 제외됨 (review_ 접두사).
- **배치 포맷**: format_review_batch — 열 순서 무관하게 `SRC=/CAT=/TGT=/REF=` 태그 부착.
- **결과 기입**: 결과열에 `OK` 또는 `수정: <제안> | 사유: <한국어>`. 행 ID 매칭은 번역과 동일.
- **검수 모드에서 비활성화되는 것**: 한글 감지 재시도(사유가 한국어라 정상),
  플레이스홀더 검증 재시도, E열 자동 표시/재검증 스윕.
- get_pending_rows 는 이제 6-튜플 (행, source, ref, placeholder, category, review) 반환.
  검수 모드에선 검수대상이 빈 행은 건너뜀.

---

## 8. 빠른 점검 명령

```bash
# 문법 검사
python -c "import ast; ast.parse(open('main.py',encoding='utf-8').read())"
python -c "import ast; ast.parse(open('main_ui.py',encoding='utf-8').read())"
python -c "import ast; ast.parse(open('updater.py',encoding='utf-8').read())"

# version.json 생성 (배포자)
python make_version.py 1.0.1

# 프롬프트 목록 확인
python -c "import main; print(main.list_prompt_langs())"
```
