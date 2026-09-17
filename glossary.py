# -*- coding: utf-8 -*-
"""
glossary.py — 용어집(Glossary) 로딩·매칭

구글 시트 한 탭에 들어있는 용어집을 읽어, 번역 배치에 두 가지를 제공한다.

  1) **플레이스홀더 확정 치환** — «T:내용» 의 내용이 용어집에 있으면 대상 언어의
     공식 용어로 바꿔서 복원한다. AI 를 전혀 거치지 않으므로 100% 확정적이다.
     (마커(«T: »)는 결과 시트에 그대로 남는다. 나중에 사람이 눈으로 확인하며
      수작업으로 떼어내면 그 안의 내용이 그대로 게임에 들어가므로, 내용은
      반드시 대상 언어의 공식 용어여야 한다)
  2) **프롬프트 지시문** — 배치에 실제로 등장하는 용어만 뽑아 "이 번역을 쓰라"는
     표를 만들어 배치 메시지에 붙인다. 문장 속 용어를 기계 치환하면 조사·성수·
     어순이 깨지므로, 치환하지 않고 모델에게 지시한다.

용어집 스키마 — 열은 **이름으로 찾으므로 순서는 상관없다**:
  ko-KR          매칭 열쇠 (필수)
  en-US zh-CN th-TH es-ES de-DE fr-FR id-ID tr-TR pt-BR es-419   대상 언어
  aliases        '|' 로 구분한 이형 표기 (조사 변형, 영어 표기 등)
  match_mode     exact | exact_or_contains | boundary_only
  protect_level  HARD(반드시) | SOFT(권장) | HINT(참고)
  priority       숫자. 겹칠 때 큰 쪽이 이긴다
  status         active 만 사용
  category term_key note   참고용
"""

import re

# ── 도구의 언어 코드 → 용어집 열 이름 ────────────────────────────────────────
LOCALE_BY_LANG = {
    "ko": "ko-KR", "en": "en-US", "zh_cn": "zh-CN", "th": "th-TH",
    "es": "es-ES", "es_la": "es-419", "de": "de-DE", "fr": "fr-FR",
    "id": "id-ID", "tr": "tr-TR", "pt": "pt-BR",
}

HEADER_KO = "ko-KR"

MATCH_EXACT    = "exact"              # 셀/토큰 전체가 용어와 같을 때만
MATCH_CONTAINS = "exact_or_contains"  # 전체 일치 또는 부분 문자열
MATCH_BOUNDARY = "boundary_only"      # 부분 문자열이되 단어 경계에서만

LEVEL_HARD = "HARD"   # 반드시 이 용어 — 확정 치환 + 프롬프트 필수 지시
LEVEL_SOFT = "SOFT"   # 권장 — 프롬프트 지시 (굴절 허용)
LEVEL_HINT = "HINT"   # 참고
_LEVEL_ORDER = {LEVEL_HARD: 3, LEVEL_SOFT: 2, LEVEL_HINT: 1}

# 단어 경계 판정용 — 한글/영문/숫자는 '단어 문자'로 본다
_WORD_CH = re.compile(r"[0-9A-Za-z가-힣ㄱ-ㆎ]")

# 한국어는 조사가 단어에 붙어버려서(예: '검' + '을') 단순 경계 검사로는 놓친다.
# 용어집의 aliases 열이 이걸 보완하지만 1.6% 행에만 있어서, 뒤쪽 경계에 한해
# 흔한 조사를 허용한다. (조사 뒤가 다시 단어 문자면 조사가 아니라고 보고 거른다)
_PARTICLES = (
    "으로써", "으로서", "이라고", "에게서", "에서는", "으로는", "이라는",
    "라고", "로써", "로서", "에게", "에서", "부터", "까지", "처럼", "보다",
    "마저", "조차", "이나", "이란", "라는", "이든", "이야", "이요",
    "은", "는", "이", "가", "을", "를", "의", "에", "도", "만", "와", "과",
    "로", "나", "야", "여", "께", "든", "란", "라",
)


def _is_word(ch):
    return bool(ch) and bool(_WORD_CH.match(ch))


def _norm(s):
    """매칭용 정규화 — 앞뒤 공백 제거. 영문은 대소문자 무시."""
    return (s or "").strip()


def _fold(s):
    return _norm(s).casefold()


class Term:
    """용어집 한 줄."""

    __slots__ = ("key", "category", "ko", "targets", "aliases",
                 "match_mode", "level", "priority", "note")

    def __init__(self, key, category, ko, targets, aliases,
                 match_mode, level, priority, note=""):
        self.key = key
        self.category = category
        self.ko = ko
        self.targets = targets          # {"es-ES": "...", ...}
        self.aliases = aliases          # [str, ...] — ko 포함
        self.match_mode = match_mode
        self.level = level
        self.priority = priority
        self.note = note

    def target(self, lang):
        """도구 언어 코드로 대상 언어 용어를 꺼낸다. 없으면 ''."""
        return self.targets.get(LOCALE_BY_LANG.get(lang, ""), "")

    def __repr__(self):
        return f"<Term {self.ko!r} {self.match_mode}/{self.level} p{self.priority}>"


class Glossary:
    """용어집 한 벌 — 로딩·색인·매칭을 담당한다."""

    def __init__(self, terms=None, tab="", skipped=0):
        self.terms = terms or []
        self.tab = tab
        self.skipped = skipped          # 열쇠(ko-KR)가 없어 버린 행 수
        self._exact = {}                # 완전 일치 색인 {folded: [Term]}
        self._by_first = {}             # 부분 일치 후보 색인 {첫 글자: [(표기, Term)]}
        self._build_index()

    # ── 로딩 ────────────────────────────────────────────────────────────────

    @classmethod
    def from_rows(cls, rows, tab=""):
        """시트 값(2차원 리스트, 1행이 헤더)에서 용어집을 만든다.

        열은 헤더 이름으로 찾는다 — 열 순서가 바뀌어도, 모르는 열이 섞여 있어도
        동작한다. ko-KR 이 비었거나 status 가 active 가 아닌 행은 버린다.
        """
        if not rows:
            return cls([], tab)
        header = [_norm(h) for h in rows[0]]
        idx = {}
        for i, h in enumerate(header):
            if h and h not in idx:      # 같은 이름이 두 번 나오면 첫 번째만
                idx[h] = i

        if HEADER_KO not in idx:
            raise ValueError(
                f"용어집 탭 '{tab}' 에 '{HEADER_KO}' 열이 없습니다. "
                f"찾은 열: {', '.join(h for h in header if h)[:200]}")

        locale_cols = {loc: idx[loc] for loc in LOCALE_BY_LANG.values() if loc in idx}

        def cell(row, name):
            i = idx.get(name)
            if i is None or i >= len(row):
                return ""
            return _norm(row[i])

        terms, skipped = [], 0
        for row in rows[1:]:
            if not row:
                continue
            ko = cell(row, HEADER_KO)
            if not ko:
                skipped += 1
                continue
            status = cell(row, "status").lower()
            if status and status != "active":
                skipped += 1
                continue

            targets = {}
            for loc, i in locale_cols.items():
                v = _norm(row[i]) if i < len(row) else ""
                if v:
                    targets[loc] = v
            if not targets:
                skipped += 1
                continue

            mode = cell(row, "match_mode").lower() or MATCH_CONTAINS
            if mode not in (MATCH_EXACT, MATCH_CONTAINS, MATCH_BOUNDARY):
                mode = MATCH_CONTAINS
            level = cell(row, "protect_level").upper() or LEVEL_SOFT
            if level not in _LEVEL_ORDER:
                level = LEVEL_SOFT      # MEDIUM 등 알 수 없는 값은 SOFT 로
            try:
                priority = int(float(cell(row, "priority") or 0))
            except ValueError:
                priority = 0

            aliases = [ko]
            for a in cell(row, "aliases").split("|"):
                a = _norm(a)
                if a and a not in aliases:
                    aliases.append(a)

            terms.append(Term(
                key=cell(row, "term_key"), category=cell(row, "category"),
                ko=ko, targets=targets, aliases=aliases,
                match_mode=mode, level=level, priority=priority,
                note=cell(row, "note")))
        return cls(terms, tab, skipped)

    # ── 색인 ────────────────────────────────────────────────────────────────

    def _build_index(self):
        """완전 일치용 dict 와, 부분 일치 후보를 좁히는 '첫 글자' 색인을 만든다.

        첫 글자 색인이 없으면 셀 하나마다 8천여 개 용어를 전부 훑어야 해서
        전수 검사가 느려진다. 셀에 실제로 등장하는 글자로 후보를 먼저 거른다.
        """
        for t in self.terms:
            for a in t.aliases:
                self._exact.setdefault(_fold(a), []).append(t)
                if t.match_mode != MATCH_EXACT:
                    self._by_first.setdefault(a[0].casefold(), []).append((a, t))
        # 긴 표기가 먼저 잡히도록(포함 관계에서 구체적인 용어가 이기도록) 정렬
        for lst in self._by_first.values():
            lst.sort(key=lambda p: (-len(p[0]), -p[1].priority))
        for lst in self._exact.values():
            lst.sort(key=lambda t: (-_LEVEL_ORDER.get(t.level, 0), -t.priority))

    def __len__(self):
        return len(self.terms)

    # ── 매칭 ────────────────────────────────────────────────────────────────

    def lookup_whole(self, text, lang):
        """문자열 '전체'가 한 용어와 같은지 본다 (플레이스홀더 확정 치환용).

        대상 언어 용어가 비어 있는 항목은 건너뛰고 다음 후보를 본다.
        반환: Term 또는 None
        """
        for t in self._exact.get(_fold(text), ()):
            if t.target(lang):
                return t
        return None

    def find_in_text(self, text, lang, limit=0):
        """문장 안에 등장하는 용어를 match_mode 규칙대로 찾는다 (프롬프트 지시용).

        - 긴 용어부터 확인하고, 이미 잡힌 구간과 겹치는 짧은 용어는 버린다.
          (예: '룬 미드가르드 왕국' 이 잡히면 그 안의 '왕국' 은 안 잡는다)
        - boundary_only 는 앞뒤가 단어 문자가 아닐 때만 인정하되, 뒤쪽은 한국어
          조사가 붙은 경우도 경계로 본다.
        - exact 는 문자열 전체가 용어와 같을 때만.
        반환: [Term] — 우선순위(HARD 먼저, priority 큰 순)로 정렬
        """
        text = text or ""
        if not text or not self.terms:
            return []

        whole = _fold(text)
        found, spans = {}, []

        # 셀 전체가 한 용어와 같은 경우 — 가장 구체적인 매칭이므로 먼저 잡고,
        # 구간 전체를 소비된 것으로 표시해 그 안의 짧은 용어가 중복으로 잡히지
        # 않게 한다. (예: '룬 미드가르드 왕국의 국왕 트리스탄 3세' 가 통째로
        #  일치하면 그 안의 '룬 미드가르드 왕국' / '트리스탄 3세' 는 버린다)
        for t in self._exact.get(whole, ()):
            if t.target(lang) and t.key not in found:
                found[t.key] = t
        if found:
            spans.append((0, len(text)))

        # 부분 일치 — 셀에 등장하는 글자로 후보를 좁힌다
        seen_chars = {c.casefold() for c in text}
        cands = []
        for ch in seen_chars:
            cands.extend(self._by_first.get(ch, ()))
        cands.sort(key=lambda p: (-len(p[0]), -p[1].priority))

        low = text.casefold()
        for surface, t in cands:
            if t.key in found:
                continue
            if not t.target(lang):
                continue
            s = surface.casefold()
            start = low.find(s)
            while start != -1:
                end = start + len(s)
                if self._boundary_ok(text, start, end, t.match_mode) \
                        and not any(start < e and s2 < end for s2, e in spans):
                    found[t.key] = t
                    spans.append((start, end))
                    break
                start = low.find(s, start + 1)

        out = sorted(found.values(),
                     key=lambda t: (-_LEVEL_ORDER.get(t.level, 0), -t.priority, t.ko))
        return out[:limit] if limit else out

    @staticmethod
    def _boundary_ok(text, start, end, mode):
        if mode == MATCH_EXACT:
            return start == 0 and end == len(text)
        if mode == MATCH_CONTAINS:
            return True
        # boundary_only
        if start > 0 and _is_word(text[start - 1]):
            return False
        if end >= len(text):
            return True
        if not _is_word(text[end]):
            return True
        # 뒤에 한국어 조사가 붙은 형태도 경계로 인정
        tail = text[end:]
        for p in _PARTICLES:
            if tail.startswith(p):
                after = tail[len(p):]
                if not after or not _is_word(after[0]):
                    return True
        return False


# ── 플레이스홀더 확정 치환 ───────────────────────────────────────────────────
#
# 마커(«T: »)는 결과 시트에 그대로 남고, 사람이 나중에 수작업으로 떼어낸다.
# 떼어내면 «T:내용» 의 '내용'이 그대로 게임에 들어가므로 내용은 '대상 언어의
# 공식 용어'여야 하는데, 지금까지는 원문(한국어)이 그대로 복사돼 왔다.
# 여기서 용어집을 보고 언어별 용어로 바꾼다. (마커 자체는 건드리지 않는다)
#
# AI 를 거치지 않는 기계 치환이므로 100% 확정적이다. 대신 '토큰 전체가 한 용어와
# 정확히 같을 때'만 바꾼다 — 토큰 안의 일부만 바꾸면 남은 부분과 어색하게 섞여
# 오히려 망가지기 때문이다. (부분 일치는 프롬프트 지시문 쪽에서 다룬다)


def _unsafe_target(value):
    """플레이스홀더 안에 넣으면 토큰 구조를 깨뜨리는 값인지 검사한다.

    «T:...» 토큰은 내부에 길리메(« »)가 없다는 전제로 파싱된다. 용어집에
    « » 가 든 값이 실제로 존재하므로(예: 'Auberge « Aube de Nordfeld »')
    그런 값은 치환하지 않고 원문을 유지한다. 줄바꿈·탭도 배치 포맷을 깨뜨린다.
    """
    return ("«" in value or "»" in value
            or "\n" in value or "\r" in value or "\t" in value)


def placeholder_substitutions(tokens, lang, gl):
    """«T:...» 토큰 목록을 받아 대상 언어로 바꿀 치환표를 만든다.

    tokens : ["«T:베넘 나이프»", ...] — 호출자가 PLACEHOLDER_RE 로 뽑아 넘긴다.
             (정규식을 한 군데(main.py)에만 두기 위해 여기서 파싱하지 않는다)

    반환: (subst, stats)
      subst = {"«T:베넘 나이프»": "«T:Cuchillo Venenoso»"}  — 바뀌는 것만 담긴다
      stats = {"changed": [(원문, 대상)], "same": [원문], "unmatched": [원문],
               "unsafe": [(원문, 대상)]}
    """
    subst = {}
    stats = {"changed": [], "same": [], "unmatched": [], "unsafe": []}
    if not gl or not tokens:
        return subst, stats

    for tok in dict.fromkeys(tokens):          # 순서 유지 + 중복 제거
        if not (tok.startswith("«T:") and tok.endswith("»")):
            continue
        inner = tok[3:-1].strip()
        if not inner:
            continue
        term = gl.lookup_whole(inner, lang)
        if term is None:
            stats["unmatched"].append(inner)
            continue
        tgt = term.target(lang)
        if tgt == inner:
            stats["same"].append(inner)
            continue
        if _unsafe_target(tgt):
            stats["unsafe"].append((inner, tgt))
            continue
        subst[tok] = f"«T:{tgt}»"
        stats["changed"].append((inner, tgt))
    return subst, stats


def apply_substitutions(texts, subst):
    """문자열 목록에 치환표를 적용한다 (토큰 단위 완전 일치 교체).

    정규식이 아니라 토큰 문자열 그대로 바꾸므로, 부분 문자열이 우연히 걸리는
    사고가 없다. 치환표가 비었으면 원본을 그대로 돌려준다.
    """
    if not subst or not texts:
        return texts
    out = []
    for t in texts:
        if t:
            for src, dst in subst.items():
                if src in t:
                    t = t.replace(src, dst)
        out.append(t)
    return out


def apply_substitutions_one(text, subst):
    """문자열 하나에 치환표를 적용한다."""
    return apply_substitutions([text], subst)[0]


# ── 프롬프트 지시문 ──────────────────────────────────────────────────────────

_LEVEL_HEAD = {
    LEVEL_HARD: "[필수] 아래 용어는 반드시 지정된 번역을 그대로 사용하십시오.",
    LEVEL_SOFT: "[권장] 아래 용어는 지정된 번역을 기준으로 하십시오. "
                "문법상 필요한 활용·관사·성수 변화는 허용됩니다.",
    LEVEL_HINT: "[참고] 아래는 기존에 쓰인 표기입니다. 참고만 하십시오.",
}


def build_glossary_block(terms, lang, max_terms=60):
    """배치에 등장한 용어들로 프롬프트에 붙일 지시문을 만든다.

    보호 등급별로 묶어 문구를 달리한다. 토큰이 무한정 늘어나지 않도록
    HARD → SOFT → HINT 순으로 max_terms 개까지만 싣는다.
    """
    if not terms:
        return ""
    picked, out = terms[:max_terms], []
    out.append("────────────────────────────────")
    out.append("[ 용어집 — 이 묶음에 등장하는 확정 용어 ]")
    out.append("────────────────────────────────")
    for level in (LEVEL_HARD, LEVEL_SOFT, LEVEL_HINT):
        group = [t for t in picked if t.level == level]
        if not group:
            continue
        out.append("")
        out.append(_LEVEL_HEAD[level])
        for t in group:
            cat = f" ({t.category})" if t.category else ""
            out.append(f"  · {t.ko}{cat}  →  {t.target(lang)}")
    if len(terms) > len(picked):
        out.append("")
        out.append(f"  (그 외 {len(terms) - len(picked)}개 용어는 지면상 생략)")
    out.append("")
    out.append("위 용어는 번역 규칙보다 우선합니다. 임의로 다른 말로 바꾸지 마십시오.")
    return "\n".join(out)
