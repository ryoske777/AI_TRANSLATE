# RO 로컬라이제이션 자동 번역 도구

ChatGPT(또는 Claude) 웹 UI를 구동해 Google Sheets의 게임 텍스트를 다국어로
자동 번역하는 도구. 라그나로크 온라인(RO) 로컬라이제이션 업무용.

---

## 사용자용 — 설치 (Python 불필요)

1. **`RO_Translator.exe` 다운로드**
   [Releases](https://github.com/ryoske777/ai_translate/releases/latest) 에서
   `RO_Translator.exe` 를 받습니다. (Python 설치 필요 없음 — 더블클릭 실행)

2. **`credentials.json` 준비**
   본인 Google 서비스 계정 키 파일을 준비합니다. (보안상 exe 에 포함하지 않습니다.)
   첫 실행 시 뜨는 **설정 마법사에서 '찾아보기'로 이 파일을 선택**하면 자동으로
   프로그램 폴더에 복사됩니다. (직접 exe 옆에 두어도 됩니다.)

3. **Chrome 설치**
   도구가 Chrome 을 띄워 ChatGPT 웹을 조작합니다. Chrome 이 설치돼 있어야 합니다.

4. **`RO_Translator.exe` 실행**
   첫 실행 시 **설정 마법사**가 떠서 Chrome 경로·credentials.json·스프레드시트
   주소·기본 설정을 차례로 안내합니다. (언제든 ⚙ → 🧙 로 다시 실행 가능)

5. **스프레드시트 공유 (필수)**
   마법사/설정창에 표시되는 **서비스 계정 이메일**(`...@....iam.gserviceaccount.com`)을
   구글 스프레드시트 우상단 **[공유]** 에 붙여넣고 **편집자**로 추가하세요.
   (또는 [공유] → '링크가 있는 모든 사용자'를 '편집자'로 변경해도 됩니다.)
   이 공유가 안 되어 있으면 시트가 열리지 않습니다.

> 폴더 구성 예시
> ```
> 작업폴더\
>   ├ RO_Translator.exe      ← 다운로드
>   ├ credentials.json       ← 직접 배치(필수, 사용자 고유)
>   ├ settings.json          ← 자동 생성
>   └ prompts\               ← 자동 생성(편집 가능)
> ```

### 자동 업데이트
새 버전이 릴리스되면 실행 시 팝업으로 안내합니다. **예**를 누르면 새 exe 를
내려받아 자동으로 교체·재시작합니다. 편집한 프롬프트는 보존됩니다.

---

## 개발자용 — 빌드 & 배포

### 로컬 실행 (개발)
```bash
pip install -r requirements.txt
python main_ui.py          # 또는 run_ui.bat (전용 프로필 Chrome 자동 기동)
```
개발(.py) 모드에서는 자동 exe 교체가 동작하지 않습니다(코드는 git pull 로 갱신).

### 로컬 exe 빌드
```bash
pip install -r requirements.txt pyinstaller
pyinstaller RO_Translator.spec
# 결과: dist/RO_Translator.exe
```

### 릴리스 (자동 빌드)
```bash
python make_version.py 1.0.1     # version.txt 갱신
git add -A && git commit -m "release v1.0.1"
git push origin main
```
`main` 에 푸시하면 GitHub Actions(`.github/workflows/release.yml`)가 Windows 에서
exe 를 빌드하고 `version.txt` 버전(`v1.0.1`)으로 릴리스에 `RO_Translator.exe` 를
첨부합니다. (Actions 탭에서 **Run workflow** 로 수동 실행도 가능)
사용자는 다음 실행 때 자동 업데이트를 안내받습니다.

---

## 파일 구성
| 파일 | 역할 |
|------|------|
| `main.py` | 번역 엔진(시트 R/W, 배치, 응답 파싱, 행 ID 매칭, 프롬프트 시드) |
| `main_ui.py` | customtkinter 데스크톱 UI + 자동 업데이트 |
| `paths.py` | 개발/exe 환경별 경로(app_dir / resource_dir) |
| `updater.py` | GitHub Releases 기반 exe 자동 업데이트 |
| `config.py` | 설정 기본값(정본은 `settings.json`) |
| `prompts/` | 언어별 번역 프롬프트(행 ID 규칙은 코드가 런타임 주입) |
| `make_version.py` | 배포자용 버전/태그 도구 |
| `RO_Translator.spec` | PyInstaller 빌드 정의 |
| `.github/workflows/release.yml` | 태그 푸시 시 exe 빌드·릴리스 |

`credentials.json` / `settings.json` 은 사용자 고유 파일이라 repo 에 포함하지 않습니다.
