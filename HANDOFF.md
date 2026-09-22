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
  다이얼로그, 연속 번역(SeqDialog), 자동 업데이트 통합.
- **glossary.py** — 용어집 로딩·매칭 + 플레이스홀더 확정 치환. (9번 섹션 참조)
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
- **결과 기입**: 모델 판정(`OK` / `수정: <제안> | 사유: <한국어>`)을 parse_review_verdict 가
  파싱해 **결과열엔 최종 단어만**(OK면 기존 번역 그대로, 수정이면 수정안),
  **결과열 바로 다음 열엔 판정 전체**(write_review_notes)를 기입. 행 ID 매칭은 번역과 동일.
  판정 형식을 해석 못 한 행은 결과열을 비워 재실행 시 재검수되게 한다.
- **검수 모드에서 비활성화되는 것**: 한글 감지 재시도(사유가 한국어라 정상),
  플레이스홀더 검증 재시도, E열 자동 표시/재검증 스윕.
- get_pending_rows 는 이제 6-튜플 (행, source, ref, placeholder, category, review) 반환.
  검수 모드에선 검수대상이 빈 행은 건너뜀.

---

## 8. 연속 번역 (2026-09 추가)

메인 화면 헤더 **🗂** → `SeqDialog` → 체크한 언어를 순서대로 자동 실행한다.

### 구조 — 왜 이렇게 짰나
번역 루프(`TranslationWorker`)를 다시 쓰지 않았다. 대신 **App 이 오케스트레이터**가
되어 단계마다 `config.PROMPT_LANG / RESULT_COL / NOTE_COL` 을 갈아끼우고
기존 워커를 한 번씩 돌린다. 워커 본문을 건드리지 않으므로 단일 언어 동작이
그대로 보존된다(회귀 테스트로 확인).

```
App._start_sequence(jobs)
  └ _seq_run_step()        # config 에 이번 단계 언어/열을 꽂고 워커 start
       └ TranslationWorker | CopyWorker
            └ done → log_queue("done") → App._poll
                 └ _seq_step_done()  → 다음 단계 or _seq_finish()
                      └ _seq_finish() : config 원복 + 버튼 복구 + SeqDoneDialog
```

- `self._seq` 가 None 이면 단일 실행(기존 경로), dict 면 연속 실행.
- `_seq["backup"]` 에 원래 (PROMPT_LANG, RESULT_COL, NOTE_COL) 를 담아 끝나면 복원한다.
  **연속 번역이 단일 언어 설정을 영구히 바꾸면 안 된다.**
- 실행 중에는 `_set_seq_controls(True)` 가 ⚙/📝/🌍/🗂 와 모드 세그먼트를 잠근다.
  (도중에 열 역할이 바뀌면 남은 언어가 엉뚱한 열에 기입됨 + `save_settings()` 가
  임시 RESULT_COL 을 모드 프리셋에 굳혀버림)
- 한 언어에서 오류가 나도 다음 언어로 계속 간다. STOP 은 `_seq["cancel"]` 을 세워
  현재 언어를 정리한 뒤 전체를 끝낸다.

### 특이사항(비고) 열 — 핵심 변경
전에는 E열이 `main.write_status` / `main_ui.reconcile_status` /
`audit_completed_rows` 에 **하드코딩**돼 있었다. 여러 언어를 한 시트에 붙이면
D/E(ko), F/G(en), H/I(zh-CN) … 처럼 쌍이 반복되므로 하드코딩을 걷어냈다.

- `main.get_note_col()` 이 단일 진실 원천.
  `config.NOTE_COL` 이 있으면 그 열, 비어 있으면 `next_col_letter(RESULT_COL)`.
- 기본 배치(결과 D)에서는 그대로 E → **기존 동작 무변화**.
- 설정창(⚙) → 열 설정에 '특이사항 기입' 칸이 생겼다(비우면 자동).
- 검수 모드 `write_review_notes` 도 같은 함수를 쓴다.

### 단계(job) 자료구조
```python
{"lang": "en", "mode": "translate"|"copy",
 "result_col": "F", "note_col": "G", "enabled": True}
```
settings.json 의 `SEQ_JOBS` 가 정본. `normalize_seq_jobs()` 가 손상/구버전 값을
복구하고, 목록에 없던 언어를 '꺼진 상태'로 뒤에 붙인다.

`validate_seq_jobs()` 가 막는 사고(시작 전 전부 검사):
열 문자 아님 / 결과열 == 특이사항열 / 단계 간 열 중복 / 입력열(A·B·C) 침범.

### ko-KR 은 '원본 복사'
원본이 한국어라 번역할 게 없다. `prompts/ko.txt` 는 만들지 않았고,
`CopyWorker` 가 AI 를 거치지 않고 입력열 → 결과열로 값을 그대로 복사한다.
가져올 열은 `SEQ_COPY_FROM`(기본 `auto` = 플레이스홀더 열 → 없으면 원본 열).
복사본은 원문과 100% 같으므로 플레이스홀더 검증·한글 감지·특이사항 표시를 하지 않는다.

> 나중에 한국어로 **진짜 번역**할 일이 생기면 `prompts/ko.txt` 를 추가하고 단계
> 방식을 'AI 번역'으로 바꾸면 된다. `main.is_korean_target()` 이 이미 그 경우의
> 한글 감지·재번역·'한글 포함' 표시를 자동으로 끈다 (결과에 한글이 있는 게 정상).

### 드래그 앤 드롭 구현 노트 (중요)
끄는 동안 위젯을 **절대 파괴/재생성하지 않는다**. 재생성하면 마우스 이벤트
스트림이 끊겨 드래그가 중간에 죽는다.
- 줄을 `place(x=0, y=i*ROW_H+PAD, relwidth=1.0)` 로 배치한다.
- 드래그 중엔 `place_configure(y=…)` 로 y 만 옮겨 미리보기를 준다.
- 버튼을 놓는 순간에만 `self.items` 순서를 확정하고 한 번 재배치한다.
- CTk 위젯은 `place(width=…/height=…)` 를 막는다 → 높이는 **생성자**에서 주고
  `pack_propagate(False)` 로 고정한다.

---

## 9. 용어집 (2026-09 추가)

`glossary.py` — 시트 한 탭의 용어집을 읽어 번역에 적용한다.
설정(⚙) → 용어집에서 `GLOSSARY_TAB` 을 지정하면 켜진다.

### 왜 필요했나
`«T:...»` 마커는 이후 파이프라인이 벗겨내고 **내용이 그대로 게임에 들어간다**.
그런데 마스킹 구조상 내용은 모든 언어에 원문 그대로 복사됐다 → 스페인어 서비스에
한국어가 박히는 구멍. 용어집에는 언어별 공식 표기가 이미 있으므로 이걸 꽂는다.
(같은 용어라도 th-TH/id-ID 는 영어 유지, es-ES/de-DE 는 번역 — 데이터가 그렇게 돼 있다)

### 적용 방식 — 두 갈래
1. **플레이스홀더 확정 치환**: `«T:내용»` 의 내용이 용어집에 **통째로** 있을 때만
   대상 언어 용어로 교체. AI 를 안 거치므로 확정적. 토큰 일부만 바꾸면 남은 부분과
   어색하게 섞이므로 **부분 치환은 하지 않는다.**
2. **프롬프트 지시문**: 문장 속 용어는 기계 치환하면 조사·성수·어순이 깨지므로
   바꾸지 않고, 배치에 등장하는 용어만 뽑아 표로 첨부(`build_glossary_block`).
   `protect_level` 에 따라 문구가 달라진다(HARD 필수 / SOFT 권장 / HINT 참고).

### 반드시 알아야 할 함정 — 검증 기준
용어집을 켜면 결과열의 `«T:...»` 는 **대상 언어 용어로 바뀌어 있다.** 따라서
플레이스홀더 검증을 원본(한국어) 그대로와 비교하면 **정상 행이 전부 불일치로 잡힌다.**
그래서 비교 대상 원본에도 같은 치환을 적용한다:
- 배치 경로: `ph_sources = gloss.apply_substitutions(batch_placeholder_sources(batch), ph_subst)`
- 전수 검증: `audit_completed_rows(..., glossary=, lang=)` → `localize_ph_cell()`
둘 다 같은 치환표를 쓰므로 항상 일치한다. (테스트로 이 함정을 고정해 뒀다)

치환은 `unmask_placeholders` **뒤에** 적용하므로 마스킹을 꺼도 동작하고,
한글 재시도·플레이스홀더 재시도 결과에도 똑같이 적용해야 한다(3곳 모두 반영됨).

### 매칭 규칙 — 용어집 스키마를 그대로 따른다
새 규칙을 만들지 않았다. 시트에 이미 있는 열을 해석할 뿐이다.
- `match_mode`: `exact`(셀 전체) / `exact_or_contains`(부분 허용) /
  `boundary_only`(단어 경계에서만 — 1~2자 용어 453건의 오탐 방지)
- `protect_level`: HARD / SOFT / HINT → 프롬프트 문구
- `priority`: 겹칠 때 큰 쪽이 이김. 긴 표기 우선 → 짧은 용어는 버림
- `aliases`: `|` 구분. 조사 변형·영어 표기를 담는 칸
- `status`: active 아니면 무시

한국어는 조사가 붙어버려(`검` + `을`) 단순 경계 검사로는 놓친다. aliases 가
이걸 보완하지만 1.6% 행에만 있어서, `_PARTICLES` 목록으로 **뒤쪽 경계에 한해**
흔한 조사를 허용한다. 조사 뒤가 다시 단어 문자면 조사가 아니라고 보고 거른다.

### 성능
- 로딩: 8,278행 0.07초. `App.load_glossary()` 가 탭 이름 기준으로 캐시하므로
  앱 실행 중 한 번만 읽는다. 연속 번역은 시작 시 한 번 읽어 전 언어가 공유한다.
- 매칭: 배치 1개(30행) 문장 스캔 22ms. `_by_first`(첫 글자 색인)로 후보를 좁힌 결과 —
  이게 없으면 셀마다 8천여 개를 전부 훑어 전수 검증이 못 쓸 만큼 느려진다.
- 플레이스홀더 전체 일치 1건 0.4µs (dict 조회).

### 안전장치
- 대상 언어 값에 길리메(`«` `»`)가 들어있으면 `«T:...»` 파싱이 깨지므로 **치환하지 않는다.**
  실제 데이터에 2건 있다(`Auberge « Aube de Nordfeld »` 등). `_unsafe_target()` 참조.
- 대상 언어 칸이 비면 치환하지 않고 원문 유지 (de/fr/tr 는 약 13% 가 빈칸).
- 용어집을 못 읽어도 번역은 진행한다 (경고만 띄우고 없이 감).

### 부수 효과 — 유용한 신호
용어집 미등록 용어는 `«T:한국어»` 가 그대로 남고, 기존 한글 감지가 그 행을
'한글 포함'으로 표시한다. 즉 **용어집을 켜면 '한글 포함' = 용어집에 없는 용어**가 된다.
실행 끝에 미등록 목록도 로그로 남는다.

---

## 10. 프롬프트 배포 사고와 수정 (2026-09) — ★ 반드시 읽을 것

### 무슨 일이 있었나
연속 번역에서 pt-BR 차례에 **유럽 포르투갈어(pt-PT) 프롬프트가 나갔다.**
repo 의 `prompts/pt.txt` 는 v1.4.10/v1.4.13 에서 pt-BR 로 바뀌어 있었는데,
사용자 PC 의 파일은 **v1.4.3 판(pt-PT)** 그대로였다. 결과물에 `económico`
같은 유럽식 철자가 섞여 나왔다.

### 원인 — ensure_external_prompts() 의 침묵 버그
```python
if seed.get(name) == chash:
    ...새 기본값으로 갱신
# else: 사용자가 편집했거나 기록 없음 → 보존   ← 여기
```
`.prompt_seed.json` 기록이 없으면 **무조건 보존**했다. 시드 기록 장치가 생기기
전에 깔린 파일은 기록이 없으므로, 편집한 적이 없어도 영원히 갱신되지 않는다.
프롬프트를 고쳐 배포해도 그 사용자에게는 전달되지 않고, **아무 경고도 없다.**

업데이터(updater.py)는 이제 exe 만 교체하므로, 프롬프트가 사용자에게 닿는
경로는 `ensure_external_prompts()` 하나뿐이다. 그게 막혀 있었다.

### 수정
1. **과거 배포본 해시표** `prompts/_known_defaults.json` 을 번들에 싣는다.
   git 이력의 모든 프롬프트 버전 해시가 들어있다(`tools/make_prompt_baseline.py`).
   사용자 파일이 그중 하나와 같으면 '한 번도 편집 안 한 옛 기본값' 이 확실하므로
   안심하고 갱신한다. → 전 언어 과거본 588개 전부 최신으로 갱신됨을 테스트로 확인.
2. **시드 기록 백필** — 파일이 이미 최신이면 기록이 없어도 지금 채워 넣는다.
3. **진짜 편집본은 보존하되 알린다** — `main.STALE_PROMPTS` 에 담고,
   앱 시작 시 경고 팝업 + 로그. 조용히 옛 프롬프트를 쓰는 상태가 사라진다.
4. **탈출구** — 📝 프롬프트 편집창에 '기본값으로 복원' 버튼
   (`restore_default_prompt` / `prompt_is_default`).
5. **재발 방지** — `make_version.py` 가 배포 때마다 해시표를 자동 재생성한다.
   **이걸 빼먹으면 다음 배포에서 같은 사고가 난다.**

### 배포자가 지킬 것
프롬프트를 고쳤으면 `python make_version.py <버전>` 을 돌리고
`prompts/_known_defaults.json` 이 **함께 커밋**되는지 확인할 것.

---

## 11. 결과열 머리글 (2026-09 추가)

`main.write_column_headers()` — 데이터 시작 행 바로 위(`START_ROW - 1`)에
결과열은 로케일(`pt-BR`), 특이사항열은 `pt-BR 특이사항` 을 적는다.
여러 언어를 붙였을 때 어느 열이 무슨 언어인지 시트에서 바로 보이게 하는 용도.

- 사용자가 직접 적은 머리글은 덮지 않는다. 빈 칸이거나 `_managed_headers()`
  (로케일 코드 / 언어 라벨 / 그 + ' 특이사항')에 해당할 때만 기입한다.
- `START_ROW == 1` 이면 머리글 자리가 없으므로 아무 것도 하지 않는다.
  (1행 데이터를 덮어쓰는 사고 방지)
- 복사 단계(ko)는 특이사항을 쓰지 않으므로 결과열 머리글만 적는다.
- `config.WRITE_HEADER` 로 끌 수 있다.

---

## 12. 빠른 점검 명령

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

---

## 13. 프롬프트 지역 기준 (2026-09 정리)

각 프롬프트는 **서비스 지역이 하나로 고정**돼 있다. 다른 지역/다른 언어의 표현이
섞이면 번역 품질이 직접 깨지므로, 프롬프트를 편집할 때 이 기준을 유지할 것.

| 코드 | 지역 기준 | 특별히 금지하는 것 |
|---|---|---|
| `en` | 미국 영어(en-US) | 영국식 철자(colour/armour/realise) |
| `es` | 유럽 스페인어(es-ES) | 중남미 어휘·voseo, 포르투갈어 혼입 |
| `pt` | **브라질 포르투갈어(pt-BR)** | 유럽식 어휘(ecrã/utilizador)·`estar a + 부정사`, 스페인어 혼입(¿ ¡ ñ) |
| `de` | 독일 표준(de-DE) | 스위스식 ss 표기, 오스트리아·스위스 고유 어휘 |
| `fr` | 프랑스 본토(fr-FR) | 퀘벡·벨기에·스위스 고유 표현 |
| `tr` | 현대 표준 튀르키예어(tr-TR) | 고유 문자를 ASCII로 대체, 아제르바이잔어·오스만 고어체 |
| `id` | 표준 인도네시아어(id-ID) | 말레이시아 말레이어, 자카르타 속어(gue/lo/nggak) |
| `zh_cn` | 중국 대륙 간체(zh-CN) | 번체·대만/홍콩 어휘, 일본식 한자 |
| `th` | 태국(th-TH) | 태국 숫자 표기, 라오어·지방 방언 |

- 포르투갈어는 **번역·검수 모두 pt-BR 하나**로 통일했다. 유럽 포르투갈어 검수 변형
  (`pt_pt`)은 제거했고, settings.json 에 남아 있으면 main_ui 의 `_legacy_langs` 가
  `pt` 로 흡수한다. (`pt_br` 구코드도 동일)
- 플레이스홀더 예시(«T:...» 안의 단어)는 **그 언어의 예시**를 쓴다. 다른 언어 프롬프트를
  복사해 만들 때 예시 단어를 같이 가져오지 말 것.
- `review_general.txt` / `review_glossary.txt` 는 전 언어 공용이므로 지역 중립으로 쓰고,
  지역 기준은 `main.REVIEW_LANG_DESC` 가 `{TGT_LANG}` 자리에 주입한다.

---

## 14. exe 실행 시 "오디널(ordinal) 380" 로더 오류 (2026-09, v1.8.0 에서 수정)

### 증상
exe 를 실행하면 `RO_Translator.exe - 오디널 찾기 실패` 창이 뜨고,
**확인을 누른 뒤 다시 실행하면 멀쩡히 동작**한다. 매번 재현되지 않는다.

### 원인 — 번들에 들어가 있던 Windows 런타임(UCRT)
v1.7.0 릴리스 exe 를 뜯어보니(PyInstaller CArchive 파싱) 다음이 함께 들어 있었다.

```
ucrtbase.dll                 10.0.26100.1742   ← 빌드 서버(Windows Server 2025)의 것
api-ms-win-crt-*.dll   (13개) 10.0.26100.1742
api-ms-win-core-*.dll  (30개) 10.0.26100.1742
VCRUNTIME140.dll             14.38.33126.1     ← 이건 OS 구성요소가 아니라 유지해야 함
```

`ucrtbase.dll` 과 `api-ms-win-*.dll` 은 **Windows 10 부터 OS 구성요소**다.
PyInstaller 는 빌드 PC 의 파이썬 폴더 옆에 이 파일들이 있으면 의존 DLL 로 보고
그대로 담는데, onefile exe 는 실행 시 내부 파일을 `%TEMP%\_MEIxxxx` 에 풀고
그 폴더를 **DLL 검색 경로 앞쪽**에 놓는다. 그래서

- 어떤 모듈은 `_MEIxxxx` 의 26100 빌드 런타임을,
- 어떤 모듈은 시스템(System32)의 사용자 PC 빌드 런타임을

물게 되고, 한 프로세스 안에 서로 다른 빌드의 CRT 가 섞인다. 어느 쪽이 먼저
잡히는지는 로드 순서(백신 후킹·주입 DLL·캐시 상태)에 좌우되므로 **"떴다 안 떴다"**
하는 증상이 된다. 로더가 export 를 못 찾으면 나오는 게 오디널/진입점 오류다.

참고로 UPX 는 원인이 아니었다. `upx=True` 였지만 GitHub Actions 러너에 upx 가
없어 실제로는 압축되지 않았다(릴리스 exe 안에 UPX 시그니처 0개). 다만 러너 이미지가
바뀌면 조용히 압축이 켜져 같은 계열의 오류를 만들 수 있어 `upx=False` 로 못박았다.

### 조치 (v1.8.0)
1. **RO_Translator.spec** — `a.binaries` 에서 `api-ms-win-*.dll` / `ucrtbase.dll` 을
   제거. OS 것을 쓰게 한다. (Windows 10 이상 필요. `VCRUNTIME140.dll` 은 유지)
   빌드 로그에 `[spec] OS 런타임 DLL N개를 번들에서 제외했습니다.` 가 찍힌다.
2. **RO_Translator.spec** — `upx=False` 로 고정(재현성·백신 오탐 방지).
3. **updater.py — 교체 방식 변경.** 실행 중이던 exe 를 `shutil.copy2` 로 그 자리에
   덮어쓰지 않는다. 방금 종료한 exe 를 같은 자리에 덮으면 Windows 이미지 캐시 +
   백신 실시간 검사와 겹쳐 '교체 직후 첫 실행'만 깨지는 일이 있다. 이제는
   `_update_new.exe` 로 완전히 쓴 뒤 → 구 exe 를 `.old` 로 밀어내고 → rename 으로
   제자리에 넣는다(같은 폴더라 원자적). 실패하면 `.old` 를 되돌려 구버전을 지킨다.
4. **updater.py — 다운로드 검증.** Content-Length / 릴리스 자산 크기 / `MZ` 서명을
   확인한 뒤에만 교체에 쓴다. 받다 만 파일로 교체해 exe 를 깨뜨리지 않기 위함.
5. **updater.py — 대기 강화.** 구 프로세스 PID 를 `--apply-update <target> <pid>` 로
   넘겨 `WaitForSingleObject` 로 기다리고, 파일 잠금이 풀릴 때까지 한 번 더 기다린다.
   (onefile 은 부트로더 + 앱 두 프로세스로 돌아 앱이 죽어도 잠금이 잠깐 남는다)
   구버전(1.7.0 이하)이 PID 없이 띄워도 동작하도록 인자는 선택이다.
6. **updater.py — `update.log`.** 교체 각 단계를 exe 옆에 기록. 다음에 업데이트가
   이상하게 끝나면 이 파일부터 본다. 찌꺼기(`_update_new.exe`, `*.old`,
   `_update_download.exe`)는 다음 실행 때 `cleanup_after_update()` 가 지운다.

### 다시 진단해야 할 때 쓰는 방법
릴리스 exe 를 받아 PyInstaller 아카이브를 직접 뜯으면 무엇이 들어갔는지 다 보인다.
exe 끝에서 `MEI\x0c\x0b\x0a\x0b\x0e` 쿠키를 찾아 TOC(`!iiiiBc` + 이름)를 훑으면
번들 목록과 각 항목(zlib) 내용을 꺼낼 수 있다. PE export/import 테이블을 같이 보면
'무엇이 무엇을 오디널로 가져오는지'까지 확인된다.

### 주의 — 위 4번 섹션은 옛 설명이다
`## 4. GitHub 자동 업데이트` 의 `version.json` / 파일 단위 교체 설명은 지금 코드와
다르다. 현재는 **GitHub Releases 태그 + exe 자산 하나를 통째로 교체**하는 방식이고,
프롬프트 보존은 `main.ensure_external_prompts()` 의 3-way 머지가 담당한다.

---

## 15. «T:...» 마커 보존 — 열 역할과 무관하게 보장 (2026-09, v1.8.0)

### 정책
`«T:내용»` 마커는 **결과열에 마커째로 남긴다.** 도구는 절대 떼지 않는다.
떼는 작업은 번역이 끝난 뒤 사람이 눈으로 확인하며 수작업으로 한다.
(떼고 나면 마커 안의 내용이 그대로 게임에 들어가므로, 용어집이 마커 '안쪽'을
대상 언어 공식 용어로 바꿔 두는 것이다 — 9번 섹션)

### 확인한 사실
코드에는 마커를 제거하는 경로가 **없다.** 실제 파이프라인을 그대로 돌려 확인했다.

```
마스킹      [비매품] «T:파란 포션»  →  [비매품] «T:1»
언마스킹    [No vendible] «T:1»     →  [No vendible] «T:파란 포션»
용어집      «T:베넘 나이프»          →  «T:Cuchillo Venenoso»   (마커 유지)
기입        write_results(lines)     →  마커 포함 그대로
```

### 그런데 마커가 사라질 수 있던 구멍 (이번에 막음)
`_PH_CELL_IDX = 3`(placeholder 역할 열)만 보던 코드가 문제였다. 열 역할에
'플레이스홀더'를 지정하지 않고 **원본(source) 열 하나로만 작업하는 시트**에서는
`row[3]` 이 항상 빈 문자열이라:

- `mask_placeholders_in_batch()` 가 아무것도 가리지 않음 → 모델이 마커 내부를 봄
- `batch_placeholder_sources()` 가 `""` 를 돌려줌 → `check_placeholder_match("")`
  는 항상 True → **모델이 마커를 지워도 검증이 조용히 통과**
- `audit_completed_rows()` 도 `ph_col is None` 이라 검사 생략

즉 그 설정에서는 마커가 사라져도 아무 표시 없이 결과열에 기입됐다.

### 조치
- `main.marker_cell_index(row)` / `main.marker_source(row)` 신설.
  행마다 **비어 있지 않은 첫 입력열**을 검증 기준으로 고른다. 우선순위는
  `placeholder(3) → source(1)`. 참조(ref) 열은 번역 원본이 아니므로 제외.
  '토큰을 든 열'이 아니라 '비어 있지 않은 열'인 이유: 번역 결과는 번역 대상
  셀에 대응하므로, 대상 셀에 없는 토큰을 원문 셀에서 가져와 요구하면 정상
  행이 불일치로 잡힌다(거짓 양성). 대상 열이 아예 비었을 때만 원문 열로 내려간다.
- `batch_placeholder_sources()` / `mask_placeholders_in_batch()` 가 이 기준을 쓴다.
  한 행에서 **한 셀만** 마스킹한다 — 같은 내용이 두 열에 있으면 서로 다른 번호가
  붙어 복원이 흔들리기 때문.
- `main_ui`: 배치 검증 게이트에서 `get_placeholder_col_letter()` 조건 제거(항상 검증),
  `get_marker_col_letters()` 로 전수 검증(`audit_completed_rows`)도 같은 우선순위 적용,
  용어집 토큰 수집·재번역 힌트도 `marker_source()` 기준으로 통일.
- **검수 모드**도 결과열에 문장을 쓴다(`finals`). 수정안이 검수 대상 문장의
  «T:...» 마커를 잃으면 로컬 복구를 시도하고, 안 되면 그 수정안을 폐기하고
  기존 번역을 유지한다(비고열에 '[도구] … 채택하지 않음' 추가). 여기서는
  마커만 본다 — `{CL:n}` 은 검수자가 바로잡는 것이 정당한 수정일 수 있는데,
  비교 기준이 원문이 아니라 검수 대상 번역문이라 정상 수정까지 폐기된다.
- `main.PLACEHOLDER_RULE_BLOCK`(모든 프롬프트에 코드가 주입하는 규칙)에 명시:
  "마커를 떼고 내용만 적는 것도 금지 / 마커 제거는 사람이 수작업 / 다만 뗀 뒤에도
  자연스럽게 읽히도록 어순·관사·띄어쓰기를 맞출 것".
- `prompts/th.txt`: "이후 파이프라인에서 마커를 제거한다", "이 마커는 나중에
  삭제됩니다" 라고만 쓰여 있어 모델이 스스로 떼어도 된다고 읽힐 여지가 있었다.
  → 다른 언어 프롬프트(de/fr/id/zh_cn/tr)와 같은 문구로 통일.
  `en/es/pt` 에도 같은 정책 문장을 추가.
- `prompts/de.txt` / `id.txt` 의 "플레이스홀더 **제거 후** 최종 용어는 …" 은
  4줄 뒤에야 "절대 임의로 없애지 마세요" 가 나와 첫 줄만 읽으면 오해 여지가
  있었다 → "나중에 사람이 떼어냈다고 가정했을 때" 로 바꿨다.
- **검수 프롬프트에는 마커 규칙이 아예 주입되지 않고 있었다.**
  `load_prompt()` 는 `PLACEHOLDER_RULE_BLOCK` 을 붙이는데 `load_review_prompt()`
  는 `REVIEW_ID_RULE_BLOCK` 만 붙였다. 검수 수정안도 결과열에 그대로 들어가므로
  `REVIEW_PLACEHOLDER_RULE_BLOCK`(검수판 문구: 수정안에 마커째로 옮겨 적을 것,
  마커가 붙어 있다는 것 자체를 지적하지 말 것)을 신설해 주입한다.

### 전체 프롬프트 점검 결과 (2026-09)
연속 번역은 언어마다 `load_prompt(lang)` 을 그대로 쓰므로 단일 언어 실행과 같다.
9개 언어 + 검수 2종의 '실제 전송본'을 모두 확인했고, 마커를 지우라고 지시하는
문구는 없다. '제거' 라는 말이 나오는 곳은 전부 **나중에 사람이 수작업으로 제거한다**
는 설명이거나 **제거된 뒤에도 자연스럽게 읽히도록 쓰라**는 지침이다.
연속 번역의 'ko 원본 복사' 단계는 AI 를 거치지 않고 셀을 그대로 복사한다.
- `glossary.py` 주석의 '이후 파이프라인이 벗겨낸다' 설명을 실제 정책으로 수정.

### 주의
이 변경으로 **예전에는 조용히 지나가던 행이 이제 '플레이스홀더 불일치' 로 잡힌다.**
없던 문제가 생긴 게 아니라 원래 있던 문제가 보이게 된 것이다. 실행 시작 시의
전수 검증(`audit_completed_rows`)도 같은 기준이라, 과거 실행분에서 마커가 빠진 행이
한 번에 드러날 수 있다. 복구 가능한 행은 코드가 결과열을 바로 고쳐 쓴다.

---

## 16. 용어집 용어에 «T:» 마커 자동 부착 (2026-09, v1.8.0)

### 왜
용어집에는 두 갈래가 있었다.

| 경우 | 동작 | 결과열 |
|---|---|---|
| 용어가 이미 `«T:...»` 안 | 기계 치환(마커 안쪽만 교체) | `«T:Espada»` |
| 용어가 **문장 속 일반 텍스트** | 프롬프트 지시표만 첨부 | `espada` — **마커 없음** |

두 번째 경로에 마커가 안 붙었다. 용어집을 구글 시트에서 읽어오기 전에는 사용자가
**번역 돌리기 전에 손으로 `«T:...»` 를 씌우는 1차 작업**을 했는데, 용어집 연동 후
그 단계가 자동화되지 않아 빠져 있었다. 이번에 그 수작업을 코드가 대신한다.

### 어떻게
`glossary.Glossary.find_spans(text, lang, levels, skip)` — 용어가 **어디에** 나왔는지
[(start, end, Term)] 로 돌려준다. (`find_in_text()` 는 '무엇이' 나왔는지만 돌려줘
프롬프트 지시용이고, 이쪽이 마커 부착용이다. 매칭 규칙은 같은 것을 쓴다)

`main.mark_glossary_terms(text, lang, gl, levels)` — 위 구간을 `«T:…»` 로 감싼다.
토큰 정규식은 main.py 에만 둔다는 원칙에 따라, 보호 구간(`«...»` / `{...}`)은
main 이 찾아 `skip` 으로 넘긴다. `main.mark_batch_glossary()` 가 배치 단위 적용.

적용 지점은 **마스킹 직전**이다. 마커만 씌워 두면 그 뒤는 기존 경로가 전부 처리한다:

```
시트        베넘 나이프를 획득했다
①마커      «T:베넘 나이프»를 획득했다     ← 입력 열에도 기입(설정)
②마스킹    «T:1»를 획득했다
③응답      Obtuviste «T:1»
④언마스킹  Obtuviste «T:베넘 나이프»
⑤확정치환  Obtuviste «T:Cuchillo Venenoso»   ← 결과열
⑥검증      기준 원본에도 ①②를 적용해 비교 → 일치
```

### 부착 규칙 — 두 갈래 (v1.10.0)
`Glossary.find_spans()` 는 한 자리를 감쌀지 두 규칙 중 **하나만** 통과하면 된다.

① **동일 표기** (`GLOSSARY_MARK_EXACT_ALWAYS`, 기본 True) — 표기가 용어와 완전히
   같은 자리는 **보호 등급을 보지 않고** 감싼다. '완전히 같다' 는
   `Glossary._exact_surface_ok()` 가 정하며 둘 중 하나다.
   - 셀 전체(앞뒤 공백 제외)가 그 용어 — `match_mode` 와 무관
   - 문장 속에서 앞뒤가 단어 경계 — 뒤쪽은 한국어 조사가 붙은 형태도 경계로
     인정한다(`_particles_only()`). 단, `match_mode=exact` 는 '셀 전체일 때만'
     이라는 뜻이므로 문장 속 등장은 동일 표기로 치지 않는다.

② **등급 규칙** (`GLOSSARY_MARK_LEVELS`, 기본 `["HARD"]`) — 표기가 달라지는
   부분 일치까지 감싸는 쪽. SOFT 를 여기까지 굳히면 성·수·격이 막혀 문장이 깨진다.

①이 없던 v1.9.x 까지는 SOFT/HINT 용어가 **용어집에 분명히 있는데도** 마커 없이
직역돼 나갔다. 연속 번역에서 언어마다 결과가 들쭉날쭉해 보이던 원인이 이것이다.

`_PARTICLES` 는 '단독 조사' 단위로만 적고 겹조사(`으로도`, `들에게는`)는
`_particles_only()` 가 최대 `_PARTICLE_CHAIN`(3)개까지 이어붙여 처리한다.
동사·형용사 어미와 헷갈리는 `고`·`며`·`지` 는 일부러 뺐다 — `검고`(검다+고)를
`검`+조사로 보면 엉뚱한 곳에 마커가 붙는다.

### 결정사항 (사용자 확인)
- **표기가 완전히 같으면 등급 무관하게 항상 부착** (v1.10.0). "용어집에 있는
  단어인데 마커가 안 붙는다" 는 보고에 대한 대응이다.
- **부분 일치는 HARD 뿐** (`glossary.MARK_LEVELS_DEFAULT`). 설정으로 바꿀 수 있다.
- **씌운 마커를 입력 열에도 기입한다** (`GLOSSARY_MARK_WRITE_BACK=True`).
  어떤 단어가 잡혔는지 시트에서 바로 보이고, 예전 수작업 흐름과 같아진다.

### 설정
| 키 | 기본 | 뜻 |
|---|---|---|
| `GLOSSARY_AUTO_MARK` | True | 마커 자동 부착 켜기/끄기 |
| `GLOSSARY_MARK_EXACT_ALWAYS` | True | 표기가 완전히 같으면 등급 무관하게 부착 |
| `GLOSSARY_MARK_LEVELS` | `["HARD"]` | 부분 일치까지 씌울 보호 등급 |
| `GLOSSARY_MARK_WRITE_BACK` | True | 씌운 마커를 입력 열에도 기입할지 |

설정창(⚙) 용어집 항목에 체크박스 3개로 노출. settings.json 에 저장된다.

### 진단 — 왜 이 용어엔 마커가 안 붙었나
`Glossary.unmarked_terms()` / `main.glossary_mark_gaps()` 가
`find_in_text()`(등장한 용어)와 `find_spans()`(감싼 자리)의 차이를 사유와 함께
돌려준다. 워커가 배치마다 모아 실행 요약 줄에 종류별로 찍는다
(`gl_gaps`). 번역 동작에는 관여하지 않는 진단용이다.

### 특이사항 표시된 행 재번역 (🧹, v1.10.0)
재번역 대상은 `main.get_pending_rows()` 가 **결과열이 비었는지로만** 정한다.
특이사항열은 출력일 뿐 입력이 아니므로, 표시된 행을 다시 돌리려면 결과열을
비워야 한다. 헤더 🧹 버튼(`_sweep_flagged`)이 그 손작업을 대신한다.

- `scan_flagged_rows(all_values, start_row, result_col, note_col)` — 시트를 한 번만
  읽고 열 쌍마다 훑는다. 반환 `{to_clear, pending, memo}`.
- `sweep_targets(scope_all)` — 현재 언어 한 쌍, 또는 연속 번역 계획에서 **켜진**
  단계 전부(결과열 중복 제거).
- `clear_ranges(sheet, ranges)` — `_clear_cells()` 와 달리 실패를 삼키지 않는다.
  '비웠다'고 잘못 보고하면 사용자가 재번역된 줄 알고 넘어가기 때문이다.

지키는 선:
- 지우는 값은 **결과열뿐**. 특이사항 표시는 남겨 둔다 — 재번역이 정상으로 끝나면
  `reconcile_status()` 가 그때 지운다. 중간에 멈춰도 무엇이 문제였는지 남는다.
- 대상은 `MANAGED_MARKS` 와 **정확히 같은** 값이 적힌 행뿐. 사람이 적은 메모는
  세기만 하고 건드리지 않는다.
- 지우기 전에 언어별 행 수를 보여주고 확인을 받는다. 되돌릴 수 없는 작업이다.
- 검수 모드에서는 막는다 (특이사항 자동 표시는 번역 모드에서만 기입된다).

### 전수 검증과의 관계 (주의)
`marked_and_localized()` 는 **`GLOSSARY_MARK_WRITE_BACK=False` 일 때만** 현재
규칙으로 마커를 다시 씌운다. 기입하는 설정(기본)에서는 시트의 입력 열 자체가
'실제로 보낸 모습'이므로 다시 씌우지 않는다 — 그러지 않으면 부착 규칙을 넓힐
때마다 예전에 끝난 행이 전부 `플레이스홀더 불일치` 로 잡힌다. 기입 설정에서도
검증 강도는 그대로다(보낸 마커가 입력 열에 남아 있으므로 모델이 지운 마커는
여전히 걸린다).

### 주의할 점
- **멱등**이다. 이미 씌워진 `«T:검»` 은 다음 번엔 보호 구간이라 건너뛴다.
  중간에 중단돼 '입력만 마커, 결과는 빈칸' 인 행이 남아도 다음 실행이 그대로 이어간다.
- 긴 용어 우선 + 겹침 배제. `룬 미드가르드 왕국` 이 잡히면 그 안의 `왕국` 은 안 잡는다.
- 한국어 조사는 마커 바깥에 남는다(`«T:베넘 나이프»를`). 조사는 번역되고 마커 안은
  그대로 복사되므로 의도한 동작이다.
- 대상 언어 번역이 비어 있는 용어는 **안 씌운다**. 씌우면 한국어가 그대로 굳어버린다.
- 별칭(aliases)으로 잡힌 자리도 씌운다. 안에 별칭 표기가 들어가지만
  `lookup_whole()` 이 별칭도 색인하므로 확정 치환이 정상 동작한다.
- 전수 검증(`audit_completed_rows`)은 `marked_and_localized()` 로 **보낸 모습과 같은
  기준**을 만들어 비교한다. 입력 열 기입을 꺼도 정상 행이 불일치로 잡히지 않는다.
- 연속 번역의 'ko 원본 복사' 단계는 용어집을 타지 않는다(셀 그대로 복사). ko 단계가
  먼저 돌면 그 열만 마커 없이 남을 수 있는데, 원문 열이라 문제되지 않는다.

---

## 17. 배포 이력 주의 — v1.8.0 이 두 번 발행됐다 (2026-09-17)

`release.yml` 은 main 에 푸시될 때마다 **version.txt 의 버전으로** 릴리스를 만들고
자산을 덮어쓴다(`overwrite_files: true`). 그래서 version.txt 를 올리지 않고 main 에
두 번 머지하면 **같은 태그의 내용물만 바뀐다.**

실제로 v1.8.0 이 두 번 발행됐다.

| 발행 | 머지 | 들어간 것 |
|---|---|---|
| 1차 | PR #45 | 로더 오류 수정(UCRT 번들 제외) + 마커 보존 구조화 |
| 2차 | PR #46 | + 프롬프트 전수 점검 + 용어집 마커 자동 부착 |

업데이터는 **태그 버전만 비교**하므로(`updater.is_newer`), 1차와 2차 사이에 업데이트한
사용자는 2차 내용물을 영영 안내받지 못한다. → v1.9.0 으로 올려 모두가 받게 한다.

**규칙: main 에 머지하기 전에 항상 `python make_version.py <새 버전>` 을 먼저 돌릴 것.**
(버전을 안 올린 채 머지하면 이 상황이 반복된다)

---

## 18. 업데이트 다운로드의 [WinError 5] (2026-09, v1.9.1)

### 증상
PC 에 따라 업데이트가 이 오류로 실패한다. 잘 되는 PC 도 있다.

```
[WinError 5] 액세스가 거부되었습니다:
'...\Downloads\_update_download.exe.part' -> '...\Downloads\_update_download.exe'
```

받는 것(.part 쓰기)은 성공했고 **이름 바꾸기(os.replace)만** 막혔다.

### 원인
권한 부족이 아니라 그 순간 그 이름을 쓸 수 없던 것이다. 흔한 두 가지:

- 방금 쓴 exe 를 **백신 실시간 검사가 잠깐 열어 두고 있다.** 다운로드·바탕화면
  폴더에서 특히 잦고, 백신 제품·정책에 따라 PC 마다 다르다 → '어떤 PC 는 되고
  어떤 PC 는 안 되는' 이유.
- 지난 번 업데이트가 남긴 `_update_download.exe` 가 잠겨 있어 덮어쓸 수 없다.
  (`cleanup_after_update()` 가 지우지 못한 경우)

### 조치 — 한 이름이 막혔다고 업데이트 전체를 실패시키지 않는다
`updater._finalize_download()` 가 세 단계로 물러선다.

1. **재시도** — 최대 20초, 0.3초에서 1.6배씩 늘려가며 `os.replace` 재시도.
   백신 검사는 대개 1~2초면 끝난다. 매 시도 전에 읽기 전용 속성도 푼다.
2. **다른 이름** — `_update_download_<pid>.exe` 로 옮긴다.
3. **받은 파일 그대로** — 이름 바꾸기 자체가 막히면 검증을 통과한 `.part` 를
   그대로 쓴다. 내용이 이미 검증(크기 + MZ)됐으므로 이름은 상관없다.

`_pick_download_path()` 는 시작할 때 기본 이름이 잠겨 있으면 번호를 붙인 이름을
고른다. `cleanup_after_update()` 는 `_update_download*` 계열을 glob 으로 전부 지운다.
실패 경로는 `update.log` 에 남는다.

### 주의
이 수정은 **1.9.1 에 들어 있다.** 증상을 겪는 PC 는 구버전 업데이터로 받아야 하므로
한 번은 같은 오류를 만날 수 있다. 재시도하거나, `_update_download.exe*` 를 지우고
다시 시도하거나, 릴리스 페이지에서 exe 를 직접 받아 덮어쓰면 된다.
