"""한글(HWP) 수식 스크립트 -> LaTeX 변환기.
규칙은 수식규칙표.csv, 명령 사전은 수식명령사전.csv에서 읽는다 (코드에 규칙을 박지 않음).
결과 상태: 변환 / 부분의심 / 원문보존. 애매하면 원문보존.
"""
import csv
import os
import re

TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
RULES_DIR = os.path.join(os.path.dirname(TOOL_DIR), "rules")    # 규칙 csv 폴더
HANGUL = re.compile(r"[\uac00-\ud7a3\u1100-\u11ff\u3130-\u318f]")
# 비ASCII 기호 -> LaTeX (표에 없는 비ASCII 글자는 \text 로 감싸고 부분의심)
UNICODE_SYMBOLS = {
    "\u00b7": r"\cdot", "\u2024": r"\cdot", "\u00d7": r"\times", "\u00f7": r"\div",
    "\u2265": r"\geq", "\u2267": r"\geq", "\u2264": r"\leq", "\u2266": r"\leq",
    "\u2260": r"\neq", "\u2248": r"\approx", "\u00b1": r"\pm", "\u221e": r"\infty",
    "\u2211": r"\sum", "\u0394": r"\Delta", "\u2192": r"\rightarrow", "\u2208": r"\in",
}
IGNORED_CHARS = {"\u00ad"}   # 소프트 하이픈(보이지 않는 글자)은 버린다
PLAIN_SYMS = set("+-=*/.,:;()[]<>|!?'@")


class Unsupported(Exception):
    """원문보존이 필요한 경우. cls: 2 = 사전에는 있으나 규칙 없음, 3 = 의심 단어, 0 = 구문 문제"""

    def __init__(self, word, cls, reason):
        super().__init__(reason)
        self.word, self.cls, self.reason = word, cls, reason


def load_rules():
    rules_exact, rules_lower = {}, {}
    with open(os.path.join(RULES_DIR, "수식규칙표.csv"), encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            item = (r["종류"], r["LaTeX"])
            if r["대소문자구분"] == "Y":
                rules_exact[r["명령"]] = item
            else:
                rules_lower[r["명령"].lower()] = item
    dictionary = set()
    with open(os.path.join(RULES_DIR, "수식명령사전.csv"), encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            dictionary.add(r["단어"].lower())
    dictionary |= {k.lower() for k in rules_exact} | set(rules_lower)
    return rules_exact, rules_lower, dictionary


RULES_EXACT, RULES_LOWER, DICTIONARY = load_rules()
# 출력에 나올 수 있는 LaTeX 명령 허용 목록 (C13에서 사용)
ALLOWED_COMMANDS = set(re.findall(r"\\[A-Za-z]+", " ".join(
    v[1] for v in list(RULES_EXACT.values()) + list(RULES_LOWER.values())))) | set(UNICODE_SYMBOLS.values()) | {
    r"\text", r"\frac", r"\left", r"\right", r"\begin", r"\end", r"\neq", r"\leq", r"\geq", r"\vert", r"\textbackslash",
    r"\,", r"\ ", r"\%", r"\$", r"\backslash", r"\{", r"\}", r"\\"}
ALLOWED_COMMANDS = {c for c in ALLOWED_COMMANDS if re.match(r"\\[A-Za-z]+$", c)}


class SpStr(str):
    """앞에 (일반) 공백이 있었던 토큰의 값. 글자 값은 똑같고 '앞에 공백이 있었다'는 표시만 다르다.
    한글 수식에서 중괄호 없는 over 의 왼쪽 피연산자는 공백 앞까지(바로 앞 덩어리)이기 때문에 필요하다."""


def tokenize(s):
    """스크립트를 토큰 목록으로 나눈다. (종류, 값)"""
    toks = []
    i, n = 0, len(s)
    after_ws = False        # 직전에 일반 공백이 있었는가
    while i < n:
        k0 = len(toks)
        ch = s[i]
        if ch in IGNORED_CHARS:
            i += 1
        elif ch.isspace():
            i += 1                      # 공백은 구분자일 뿐
            after_ws = True
            continue
        elif ch == '"':
            j = s.find('"', i + 1)
            if j < 0:
                raise Unsupported("\"", 0, "따옴표 짝 안 맞음")
            toks.append(("str", s[i + 1:j]))
            i = j + 1
        elif ch in "{}":
            toks.append(("open" if ch == "{" else "close", ch)); i += 1
        elif ch == "_":
            toks.append(("sub", ch)); i += 1
        elif ch == "^":
            toks.append(("sup", ch)); i += 1
        elif ch == "#":
            toks.append(("hash", ch)); i += 1
        elif ch == "&":
            toks.append(("amp", ch)); i += 1
        elif ch == "~":
            toks.append(("space", "~")); i += 1
        elif ch == "`":
            toks.append(("space", "`")); i += 1
        elif s.startswith("!=", i):
            toks.append(("rawtex", r"\neq")); i += 2
        elif s.startswith("<=", i):
            toks.append(("rawtex", r"\leq")); i += 2
        elif s.startswith(">=", i):
            toks.append(("rawtex", r"\geq")); i += 2
        elif ch.isdigit():
            m = re.match(r"\d+(?:\.\d+)?", s[i:])
            toks.append(("num", m.group(0))); i += m.end()
        elif re.match(r"[A-Za-z]", ch):
            m = re.match(r"[A-Za-z]+", s[i:])
            toks.append(("word", m.group(0))); i += m.end()
        elif HANGUL.match(ch):
            m = re.match(r"[\uac00-\ud7a3\u1100-\u11ff\u3130-\u318f]+", s[i:])
            toks.append(("han", m.group(0))); i += m.end()
        elif ch in UNICODE_SYMBOLS:
            toks.append(("rawtex", UNICODE_SYMBOLS[ch])); i += 1
        elif ch in PLAIN_SYMS:
            toks.append(("sym", ch)); i += 1
        elif ch == "%":
            toks.append(("rawtex", r"\%")); i += 1
        elif ch == "$":
            toks.append(("rawtex", r"\$")); i += 1
        elif ch == "\\":
            toks.append(("rawtex", r"\backslash")); i += 1
        else:
            toks.append(("other", ch)); i += 1      # 처리 못 하는 비ASCII 글자
        if after_ws and len(toks) > k0:
            kind0, val0 = toks[-1]
            toks[-1] = (kind0, SpStr(val0))         # 이 토큰 앞에 공백이 있었다고 표시
            after_ws = False
    return toks


class Node:
    """파싱 결과 한 덩어리. kind: group / word / num / text / sym / space / frac / op ..."""

    def __init__(self, kind, tex, simple=False):
        self.kind, self.tex, self.simple = kind, tex, simple
        self.children = None      # group 이면 안쪽 노드 목록
        self.base = self.sub = self.sup = None   # script 이면 밑/아래첨자/위첨자


class Parser:
    def __init__(self, toks, stats):
        self.t, self.i, self.stats = toks, 0, stats
        self.notes = []          # 부분의심 사유
        self.vars = []           # 변수로 취급한 단어
        self.brace_words = []    # 바로 뒤에 { 가 온 변수 후보 단어
        self.unsupported_words = []

    def peek(self, k=0):
        return self.t[self.i + k] if self.i + k < len(self.t) else (None, None)

    # ---- 시퀀스: until 종류가 나올 때까지 노드 목록 -------------------
    def parse_seq(self, stop=()):
        nodes = []
        starts = []         # nodes 와 나란히: 그 노드의 첫 토큰 앞에 공백이 있었는가
        while self.i < len(self.t):
            kind, val = self.peek()
            if kind in stop:
                break
            if kind == "word" and val.lower() == "right" and "right" in stop:
                break
            if kind == "close":
                raise Unsupported("}", 0, "닫는 중괄호가 남음")
            if kind == "word" and val.lower() == "over" and self._is_rule(val, "이항"):
                self.i += 1
                if not nodes:
                    raise Unsupported(val, 0, "over 왼쪽 피연산자 없음")
                left = nodes.pop()
                lsp = starts.pop()
                if not self._operand_ok(left):
                    # 중괄호 없는 over: 한글 수식에서는 공백(~, `, 일반 공백) 앞까지 이어진 덩어리가 왼쪽이다.
                    # 예: 통행거리(km) over {평균연비(km/l)}  ->  왼쪽 = 통행거리(km)
                    run = [left]
                    while not lsp and nodes and nodes[-1].kind != "space":
                        run.insert(0, nodes.pop())
                        lsp = starts.pop()
                    if len(run) > 1:
                        left = Node("group", "{" + "".join(n.tex for n in run) + "}")
                    else:
                        left = run[0]
                right = self.parse_term()
                nodes.append(self._make_frac(left, right, len(self.t)))
                starts.append(lsp)
                continue
            sp = isinstance(val, SpStr)
            node = self.parse_term()
            if node is not None:
                nodes.append(node)
                starts.append(sp)
        return nodes

    @staticmethod
    def _operand_ok(nd):
        return nd.kind == "group" or (nd.kind in ("word", "num") and nd.simple)

    def _make_frac(self, left, right, _):
        for side, nd in (("왼쪽", left), ("오른쪽", right)):
            if nd.kind == "group":
                continue
            if nd.kind in ("word", "num") and nd.simple:
                continue
            raise Unsupported("over", 0, "중괄호 없는 over %s 피연산자(%s)" % (side, nd.tex[:20]))
        return Node("frac", r"\frac{%s}{%s}" % (strip_braces(left.tex), strip_braces(right.tex)))

    def _is_rule(self, word, kind):
        r = RULES_EXACT.get(word) or RULES_LOWER.get(word.lower())
        return bool(r) and r[0] == kind

    # ---- 항: 원자 + 위/아래 첨자 -----------------------------------------
    def parse_term(self):
        node = self.parse_atom()
        if node is None:
            return None
        if node.kind == "space":
            return node
        sub = sup = None
        while self.peek()[0] in ("sub", "sup"):
            kind = self.peek()[0]
            self.i += 1
            arg = self.parse_script_arg()
            if (kind == "sub" and sub is not None) or (kind == "sup" and sup is not None):
                # 같은 종류 첨자가 두 번: 지금까지를 한 덩어리로 감싼다
                node = Node("group", "{" + self._compose(node, sub, sup) + "}")
                sub = sup = None
            if kind == "sub":
                sub = arg
            else:
                sup = arg
        if sub is not None or sup is not None:
            tex = self._compose(node, sub, sup)
            sn = Node("script", tex)
            sn.base, sn.sub, sn.sup = node, sub, sup
            return sn
        return node

    @staticmethod
    def _compose(base, sub, sup):
        tex = base.tex if base.kind != "group" else base.tex
        if sub:
            tex += "_{%s}" % sub
        if sup:
            tex += "^{%s}" % sup
        return tex

    def parse_script_arg(self):
        """첨자 인수: 그룹 또는 원자 하나. 빈 인수는 ''"""
        while self.peek()[0] == "space":
            self.i += 1
        if self.i >= len(self.t):
            raise Unsupported("_", 0, "첨자 인수 없음")
        node = self.parse_atom()
        if node is None:
            raise Unsupported("_", 0, "첨자 인수 없음")
        inner = strip_braces(node.tex) if node.kind == "group" else node.tex
        # 첨자 안의 첨자도 처리 (예: x^{y}가 아닌 a_b^c 형태는 바깥 루프가 다룸)
        return inner

    # ---- 원자 --------------------------------------------------------------
    def parse_atom(self):
        kind, val = self.peek()
        if kind is None:
            return None
        self.i += 1
        if kind == "open":
            nodes = self.parse_seq(stop=("close",))
            if self.peek()[0] != "close":
                raise Unsupported("{", 0, "중괄호 짝 안 맞음")
            self.i += 1
            gn = Node("group", "{" + join_nodes(nodes) + "}")
            gn.children = nodes
            return gn
        if kind == "num":
            return Node("num", val, simple=True)
        if kind == "han":
            return Node("text", r"\text{%s}" % val)
        if kind == "str":
            safe = val.replace("`", " ").replace("~", " ")
            safe = re.sub(r"([{}\\%$&#_^])", lambda m: "\\" + m.group(1) if m.group(1) not in "\\" else r"\textbackslash{}", safe)
            return Node("text", r"\text{%s}" % safe)
        if kind == "rawtex":
            return Node("sym", val)
        if kind == "sym":
            return Node("sym", val)
        if kind == "space":
            return Node("space", val)
        if kind in ("sub", "sup"):
            # 앞에 밑이 없는 첨자
            self.i -= 1
            return Node("empty", "{}")
        if kind == "hash":
            raise Unsupported("#", 0, "줄바꿈(#)이 최상위가 아닌 곳에 있음")
        if kind == "amp":
            raise Unsupported("&", 0, "정렬(&)이 최상위가 아닌 곳에 있음")
        if kind == "other":
            self.notes.append("처리 못 하는 글자 U+%04X 를 글자로 둠" % ord(val))
            return Node("text", r"\text{%s}" % val)
        if kind == "word":
            return self.parse_word(val)
        raise Unsupported(str(val), 0, "알 수 없는 토큰 " + kind)

    def parse_word(self, word):
        rule = RULES_EXACT.get(word) or RULES_LOWER.get(word.lower())
        if rule:
            rkind, tex = rule
            if rkind in ("기호", "함수"):
                return Node("sym", tex)
            if rkind == "큰연산자":
                return Node("op", tex)
            if rkind == "단항":
                while self.peek()[0] == "space":
                    self.i += 1
                arg = self.parse_atom()
                if arg is None:
                    raise Unsupported(word, 0, "단항 명령 인수 없음")
                # 위·아래 첨자가 붙은 밑 하나를 감싼 그룹이면 모양표시는 밑에만 씌운다 (PDF 대조: P̂ 아래첨자 ij)
                if (re.match(r"^\\(hat|bar|vec|dot|ddot|tilde)\{%s\}$", tex) and arg.kind == "group"
                        and arg.children and len(arg.children) == 1 and arg.children[0].kind == "script"
                        and arg.children[0].base.kind in ("word", "sym", "num")):
                    s0 = arg.children[0]
                    return Node("script", self._compose(Node("group", tex % s0.base.tex), s0.sub, s0.sup))
                return Node("group", tex % strip_braces(arg.tex))
            if rkind == "구분자" and word.lower() == "left":
                return self.parse_left_right()
            if rkind == "구분자":
                raise Unsupported(word, 0, "right 짝이 없음")
            if rkind == "이항":
                raise Unsupported(word, 0, "over 위치 오류")
        lw = word.lower()
        if lw in DICTIONARY:
            self.unsupported_words.append((word, 2))
            raise Unsupported(word, 2, "사전에 있으나 규칙 없음: " + word)
        # 사전에도 없음: 변수 이름으로 본다. 단 전부 대문자 3자 이상이면 의심
        # 예외(사용자 지시 2026-11-12): 바로 뒤에 아래 첨자(_)가 오면 변수 이름으로 본다 (예: UZA _{-} Dum).
        # 한글 수식 명령(RARROW, TRIANGLE 등)은 뒤에 첨자가 붙지 않는다.
        if len(word) >= 3 and word.isupper() and self.peek()[0] != "sub":
            self.unsupported_words.append((word, 3))
            raise Unsupported(word, 3, "전부 대문자 3자 이상(명령 의심): " + word)
        if self.peek()[0] == "open":
            self.brace_words.append(word)
        self.vars.append(word)
        return Node("word", word, simple=True)

    def parse_left_right(self):
        """left ( ... right ) 형태"""
        kind, val = self.peek()
        if kind not in ("sym", "other", "rawtex") or (kind == "sym" and val not in "()[]|"):
            raise Unsupported("left", 0, "left 다음 구분자 이상")
        self.i += 1
        ltex = {"|": r"\vert"}.get(val, val)
        inner = self.parse_seq(stop=("right",))
        k2, v2 = self.peek()
        if not (k2 == "word" and v2.lower() == "right"):
            raise Unsupported("left", 0, "left/right 짝 안 맞음")
        self.i += 1
        kind, val = self.peek()
        if kind not in ("sym", "other", "rawtex") or (kind == "sym" and val not in "()[]|"):
            raise Unsupported("right", 0, "right 다음 구분자 이상")
        self.i += 1
        rtex = {"|": r"\vert"}.get(val, val)
        return Node("group", r"\left%s %s \right%s" % (ltex, join_nodes(inner), rtex))


def strip_braces(tex):
    """바깥 중괄호 한 겹 벗기기 ({a}b{c} 같은 경우는 벗기지 않음)"""
    if tex.startswith("{") and tex.endswith("}"):
        depth = 0
        for k, ch in enumerate(tex):
            depth += (ch == "{") - (ch == "}")
            if depth == 0 and k < len(tex) - 1:
                return tex
        return tex[1:-1]
    return tex


def join_nodes(nodes):
    """노드를 이어 붙인다. 공백 노드는 연속을 합치고 양끝은 버린다."""
    out = []
    pending_space = None
    for nd in nodes:
        if nd.kind == "space":
            if pending_space is None:
                pending_space = nd.tex
            elif nd.tex == "~":
                pending_space = "~"
            continue
        if nd.kind == "empty":
            nd = Node("empty", "{}")
        if pending_space is not None and out:
            out.append(r"\ " if pending_space == "~" else r"\,")
        pending_space = None
        out.append(nd.tex)
    # 명령 뒤에 영문이 바로 오면 공백 하나가 필요
    res = ""
    for piece in out:
        if res and re.search(r"\\[A-Za-z]+$", res) and re.match(r"[A-Za-z]", piece):
            res += " "
        res += piece
    return res


def check_latex(tex):
    """C13 형식 검사: 중괄호 짝, begin/end, left/right, 허용 명령. 문제 목록 반환"""
    problems = []
    depth = 0
    s = re.sub(r"\\[{}]", "", tex)
    for ch in s:
        depth += (ch == "{") - (ch == "}")
        if depth < 0:
            break
    if depth != 0:
        problems.append("중괄호 짝 불일치")
    if len(re.findall(r"\\begin\{", tex)) != len(re.findall(r"\\end\{", tex)):
        problems.append("begin/end 짝 불일치")
    if len(re.findall(r"\\left\b", tex)) != len(re.findall(r"\\right\b", tex)):
        problems.append("left/right 짝 불일치")
    for c in set(re.findall(r"\\[A-Za-z]+", tex)):
        if c not in ALLOWED_COMMANDS:
            problems.append("허용 목록 밖 명령 " + c)
    return problems


def split_top_level(toks):
    """최상위의 # 로 줄을 나눈다 (중괄호 안은 제외)"""
    rows, cur, depth = [], [], 0
    for tk in toks:
        if tk[0] == "open":
            depth += 1
        elif tk[0] == "close":
            depth -= 1
        if tk[0] == "hash" and depth == 0:
            rows.append(cur)
            cur = []
        else:
            cur.append(tk)
    rows.append(cur)
    return rows


def convert(script, stats=None):
    """한글 수식 -> LaTeX.
    반환: dict(latex, status, reasons, unsupported(list of (단어,분류)), variables, display)
    status: 변환 / 부분의심 / 원문보존
    """
    res = {"latex": "", "status": "변환", "reasons": [], "unsupported": [], "variables": [],
           "brace_words": [], "display": False}
    try:
        toks = tokenize(script)
        if not toks:
            res["status"] = "원문보존"
            res["reasons"].append("빈 수식")
            return res
        rows = split_top_level(toks)
        notes = []
        row_tex = []
        for r in rows:
            p = Parser(r, stats)
            amp_in_row = False
            # 최상위 & 처리: 토큰을 &로 나눠 각각 파싱
            cells, cur, depth = [], [], 0
            for tk in r:
                if tk[0] == "open":
                    depth += 1
                elif tk[0] == "close":
                    depth -= 1
                if tk[0] == "amp" and depth == 0:
                    cells.append(cur)
                    cur = []
                    amp_in_row = True
                else:
                    cur.append(tk)
            cells.append(cur)
            parts = []
            for c in cells:
                pc = Parser(c, stats)
                nodes = pc.parse_seq()
                if pc.i != len(pc.t):
                    raise Unsupported("", 0, "해석하지 못한 토큰이 남음")
                parts.append(join_nodes(nodes))
                notes += pc.notes
                res["variables"] += pc.vars
                res["brace_words"] += pc.brace_words
            row_tex.append((" & ".join(parts), amp_in_row))
        # 뒤쪽 빈 줄 제거, 내용 없는 줄은 건너뜀
        row_tex = [(t, a) for t, a in row_tex if t.strip() and t.strip() != "&"]
        if not row_tex:
            res["status"] = "원문보존"
            res["reasons"].append("내용 없음")
            return res
        multi = len(rows) > 1 or any(a for _, a in row_tex)
        if multi:
            body = r" \\ ".join(t for t, _ in row_tex)
            latex = r"\begin{aligned} " + body + r" \end{aligned}"
            res["display"] = True
        else:
            latex = row_tex[0][0]
        problems = check_latex(latex)
        if problems:
            raise Unsupported("", 0, "LaTeX 형식 검사 실패: " + ",".join(problems))
        res["latex"] = latex
        if notes:
            res["status"] = "부분의심"
            res["reasons"] += sorted(set(notes))
    except Unsupported as e:
        res["status"] = "원문보존"
        res["reasons"].append(e.reason)
        if e.cls in (2, 3):
            res["unsupported"].append((e.word, e.cls))
    return res


def convert_many(scripts, extra_scripts=()):
    """여러 수식을 변환하고 7.3의 3번 규칙(뒤에 {가 오는데 변수로는 안 쓰이는 단어)을 코퍼스 전체로 적용한다."""
    results = [convert(s) for s in scripts]
    plain = {}
    brace = {}
    for r in results + [convert(s) for s in extra_scripts]:
        for w in r["variables"]:
            plain[w] = plain.get(w, 0) + 1
        for w in r["brace_words"]:
            brace[w] = brace.get(w, 0) + 1
    suspicious = {w for w, b in brace.items() if plain.get(w, 0) - b <= 0}
    for r in results:
        if r["status"] == "원문보존":
            continue
        bad = [w for w in set(r["brace_words"]) if w in suspicious]
        if bad:
            r["status"] = "원문보존"
            r["latex"] = ""
            r["reasons"].append("뒤에 {가 오지만 변수로는 쓰이지 않는 단어(명령 의심): " + ",".join(sorted(bad)))
            r["unsupported"] += [(w, 3) for w in sorted(bad)]
    return results

