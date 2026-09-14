# tools

## json_to_xlsx.html — 번역 JSON → 엑셀 변환기

게임 텍스트 추출 JSON(`itemInfo.xlsx`, `Npcdata.xlsx` … 를 최상위 키로 갖는 구조)을
엑셀 파일로 변환하는 단일 HTML 도구. 설치 없이 **브라우저로 파일을 열어** 사용합니다.
(엑셀 생성에 SheetJS CDN을 사용하므로 인터넷 연결 상태에서 열어야 합니다.)

### 사용법
1. `tools/json_to_xlsx.html` 더블클릭 → 브라우저에서 열림
2. JSON을 붙여넣거나 `.json` 파일을 끌어다 놓기
3. **변환 미리보기**로 확인 → **다운로드**
   - *통합 파일 1개*: 최상위 키마다 시트가 나뉜 `translations_날짜.xlsx`
   - *각각 저장*: `itemInfo.xlsx`, `Npcdata.xlsx` … 키 이름 그대로 개별 파일

### 출력 구조
| 위치 | 내용 |
|---|---|
| 1행 1열 | `.xlsx` 파일명 (최상위 키) |
| 1행 나머지 | `type`(+`type2`…) → `ko-KR`, `en-US` … 언어 코드 |
| 2행부터 1열 | ID (숫자/영문 키). 같은 ID의 여러 행은 첫 행에만 표시 |

국가 코드(`ko-KR`, `en-US`, `es-419` …)는 JSON에서 몇 단계 아래에 있든 **항상 1행의 열**이
되도록 그 단계를 찾아 가로로 펼칩니다. 나머지 단계는 `type` 열로 내려갑니다.

| JSON 구조 | 예 | 열 구성 |
|---|---|---|
| `id → 언어` | `Npcdata`, `mobdef`, `SkillDescript`, `msgstring` | ID + 언어 |
| `id → 필드 → 언어` | `itemInfo`, `mapInfo`, `OngoingQuestInfoList` | ID + `type` + 언어 |
| `id → 키 → 필드 → 언어` | `StateIconInfo` | ID + `type` + `type2` + 언어 |
| `id → 필드 → 언어 → {prev,curr}` | 변경분(diff) 추출 JSON | ID + `type` + `type2`(prev/curr) + 언어 |

마지막 경우는 언어 아래의 `prev`/`curr` 가 `type2` 열로 내려가므로, 한 행에 전 언어의
`prev`, 다음 행에 전 언어의 `curr` 가 놓입니다. (언어는 계속 열로 유지)

### 셀 서식
출력되는 모든 셀은 **텍스트 서식(`@`)** + 문자열 타입으로 기록됩니다. 앞자리 0(`00123`),
긴 숫자 ID, `2024-01-02` 같은 날짜형 문자열, `=` 로 시작하는 값이 엑셀에서 숫자·날짜·수식으로
자동 변환되지 않습니다. "숫자가 텍스트로 저장됨" 경고 표시(초록 삼각형)도 억제됩니다.

### 옵션
- **모든 행에 ID 반복** — 필터/정렬용으로 빈 칸 없이 ID를 채움
- **표준 언어 열 모두 표시** — 데이터에 없는 언어도 빈 열로 생성
- **내용이 빈 행 제외** — 모든 언어 칸이 비어 있는 행(예: 값이 없는 `curr`)을 숨김

### 코드 구조
변환 로직은 `<script id="core">` 블록에 UI와 분리돼 있어 node 에서 그대로 불러
테스트할 수 있습니다 (`vm.runInNewContext` + `module.exports`).
