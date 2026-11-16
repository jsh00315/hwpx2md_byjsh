"""IR -> markdown. 일반 규칙만 쓴다(문서별 숫자·줄 번호 없음).
sentinel=True 이면 원문 t 글자를 보이지 않는 표지(U+E000/E004 ... U+E001)로 감싸 C1 글자 회계에 쓴다.
markers=True 이면 점검용 표지(<!--EQ0007--> 등)를 넣는다.
"""
import collections
import csv
import html
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hwpx_common import RULES_DIR  # noqa: E402
from hwpx_reader import Box, Cell, Eq, Foot, Para, Pic, Table, Uns  # noqa: E402

S_MD, S_HT, S_END = "", "", ""
S_CELL = ""   # 수식 칸(문단으로 처리되는 HTML 표 칸) 글자 표지


def load_list_symbols():
    with open(os.path.join(RULES_DIR, "목록기호.csv"), encoding="utf-8-sig", newline="") as f:
        return {r["기호"] for r in csv.DictReader(f)}


LIST_SYMBOLS = load_list_symbols()
MD_ESC = re.compile(r"([\\`*~$\[\]])")
MD_UNESC = re.compile(r"\\([\\`*~$\[\]|#>+\-.)(!_])")


def unescape_md(s):
    return MD_UNESC.sub(r"\1", s)


def image_rel(src):
    """BinData 파일 -> 결과 폴더 상대 경로. bmp는 png로 바꿔 저장한다."""
    base = os.path.basename(src)
    stem, ext = os.path.splitext(base)
    if ext.lower() in (".bmp", ".tif", ".tiff"):
        ext = ".png"
    return "images/" + stem + ext


class Writer:
    def __init__(self, doc, markers=False, sentinel=False):
        self.doc, self.markers, self.sentinel = doc, markers, sentinel
        self.stats = {"headings": [], "heading_method": "", "list_items": 0, "images": 0,
                      "image_missing": 0, "uns_marks": 0, "footnote_refs": 0,
                      "removed_chars": collections.Counter()}
        self.heading_of = {}      # id(Para) -> 단계
        self.list_level = {}      # id(Para) -> 들여쓰기 단계
        self.hf_done = False
        self.prepare()

    # ---------------------------------------------------------- 글자 처리
    def T(self, s, mode):
        """원문 글자 s를 모드에 맞게 이스케이프하고 (sentinel이면) 표지로 감싼다."""
        s = s.replace("\n", " ").replace("\r", " ")
        if mode == "html":
            # 글자 $ 는 &#36; 로 쓴다: 한 칸에 $ 가 두 번 나오면 수식 구분 기호로 오인되는 것을 막는다
            body = html.escape(s, quote=False).replace("$", "&#36;")
            return (S_HT + body + S_END) if self.sentinel else body
        if mode == "cell":
            # 수식 칸: 칸 내용이 마크다운 문단으로 처리되므로 마크다운 기호와 HTML 기호를 모두 이스케이프
            body = html.escape(MD_ESC.sub(r"\\\1", s), quote=False)
            return (S_CELL + body + S_END) if self.sentinel else body
        body = MD_ESC.sub(r"\\\1", s)
        if mode == "pipe":
            body = body.replace("|", "\\|")
        return (S_MD + body + S_END) if self.sentinel else body

    @staticmethod
    def lead_escape(text):
        """문단 맨 앞에서 마크다운 구문으로 읽히는 글자를 이스케이프(원문 글자 기준)"""
        if text.startswith("#") or text.startswith(">") or re.match(r"[+\-] ", text):
            return "\\" + text
        m = re.match(r"(\d+)([.)])", text)
        if m:
            return m.group(1) + "\\" + text[len(m.group(1)):]
        return text

    # ---------------------------------------------------------- 문단 분류 준비
    def body_paras(self):
        return [b for b in self.doc.blocks if isinstance(b, Para) and b.ctx == "body"]

    def para_text(self, p):
        """분류용 순수 글자(첫 글자 판정용)"""
        parts = []
        for it in p.items:
            if it[0] == "text":
                parts.append(it[1])
            elif it[0] in ("eq", "pic", "uns"):
                parts.append("\u0000")
        return "".join(parts)

    def prepare(self):
        doc = self.doc
        paras = self.body_paras()
        # 1) 개요 수준 (header.xml paraPr heading OUTLINE)
        outline = {}
        for p in paras:
            pp = doc.para_pr.get(p.ppr)
            if pp and pp["heading"][0] == "OUTLINE" and self.para_text(p).strip():
                outline[id(p)] = pp["heading"][1]
        # 2) 스타일 이름 "개요 n" / "제목 n"
        style = {}
        for p in paras:
            nm = doc.styles.get(p.style, "")
            m = re.match(r"^(개요|제목)\s*(\d+)$", nm.strip())
            if m and self.para_text(p).strip():
                style[id(p)] = int(m.group(2))
        if outline:
            self.stats["heading_method"] = "개요수준"
            levels = outline
        elif style:
            self.stats["heading_method"] = "스타일이름"
            levels = style
        else:
            levels = self.pattern_headings(paras)
            self.stats["heading_method"] = "글자패턴" if levels else "없음"
        # 단계를 사용된 값의 순위로 압축
        used = sorted(set(levels.values()))
        rank = {v: i + 1 for i, v in enumerate(used)}
        for p in paras:
            if id(p) in levels:
                self.heading_of[id(p)] = rank[levels[id(p)]]
        # 목록 단계: 목록 기호로 시작하는 문단의 왼쪽 여백 순위
        lefts = set()
        for p in paras:
            if id(p) not in self.heading_of and self.list_symbol(p):
                lefts.add(doc.para_pr.get(p.ppr, {}).get("left", 0))
        lrank = {v: i for i, v in enumerate(sorted(lefts))}
        for p in paras:
            if id(p) not in self.heading_of and self.list_symbol(p):
                self.list_level[id(p)] = lrank[doc.para_pr.get(p.ppr, {}).get("left", 0)]

    PATTERNS = [
        ("장", re.compile(r"^제\s*\d+\s*장")),
        ("절", re.compile(r"^제\s*\d+\s*절")),
        ("N.", re.compile(r"^\d+\.(\s|$)")),
        ("가.", re.compile(r"^[가-하]\.(\s|$)")),
        ("N)", re.compile(r"^\d+\)(\s|$)")),
        ("(N)", re.compile(r"^(\(\d+\)|[가-하]\))(\s|$)")),
    ]

    def pattern_headings(self, paras):
        found = {}
        for p in paras:
            if any(it[0] in ("tab", "eq", "pic", "uns") for it in p.items):
                continue                      # 목차 줄(탭)·개체 포함 줄은 제목 후보에서 뺀다
            text = "".join(it[1] for it in p.items if it[0] == "text").strip()
            if not text or len(text) > 60:
                continue
            if re.search(r"(다|함|음|됨|임)\.?$", text):
                continue
            for k, (name, rx) in enumerate(self.PATTERNS):
                if rx.match(text):
                    found[id(p)] = k
                    break
        return found

    def list_symbol(self, p):
        for it in p.items:                       # 앞쪽 공백뿐인 글자 조각은 건너뛴다
            if it[0] != "text":
                return None
            s = it[1].lstrip()
            if not s:
                continue
            return s[0] if s[0] in LIST_SYMBOLS else None
        return None

    # ---------------------------------------------------------- 인라인
    def marker(self, text):
        return "<!--%s-->" % text if self.markers else ""

    def inline(self, items, mode, strip_list_symbol=False, line_start=True):
        out = []
        links = []
        first = True
        i = 0
        n = len(items)
        while i < n:
            it = items[i]
            k = it[0]
            if k == "text":
                s = it[1]
                if first and line_start:
                    s = s.lstrip()
                    if strip_list_symbol and s and s[0] in LIST_SYMBOLS:
                        self.stats["removed_chars"][s[0]] += 1
                        s = s[1:].lstrip()
                    if not s:
                        i += 1
                        continue
                    out.append(self.T_start(s, mode))
                else:
                    out.append(self.T(s, mode))
                if s.strip():
                    first = False
            elif k == "tab":
                out.append(" ")
            elif k == "lb":
                out.append(" " if mode == "head" else "<br>")
            elif k == "auto":
                out.append(it[1])
                first = False
            elif k == "eq":
                out.append(self.eq_str(it[1], mode))
                first = False
            elif k == "pic":
                out.append(self.pic_str(it[1], mode))
                first = False
            elif k == "fn":
                self.stats["footnote_refs"] += 1
                out.append("[^%d]" % (self.doc.footnotes.index(it[1]) + 1))
                first = False
            elif k == "fb":
                if it[1] == "HYPERLINK" and it[2]:
                    out.append("[" if mode != "html" else '<a href="%s">' % html.escape(it[2]))
                    links.append(it[2])
                else:
                    links.append(None)
            elif k == "fe":
                u = links.pop() if links else None
                if u:
                    out.append("](%s)" % u.replace(")", "%29") if mode != "html" else "</a>")
            elif k == "uns":
                # 같은 종류가 연달아 나오면 묶는다
                u = it[1]
                cnt = 1
                while (i + 1 < n and items[i + 1][0] == "uns" and items[i + 1][1].name == u.name
                       and not items[i + 1][1].text and not u.text):
                    cnt += 1
                    i += 1
                self.stats["uns_marks"] += 1
                label = "[미지원: %s %d개]" % (u.name, cnt)
                if mode == "html":
                    label = html.escape(label)
                txt = (" " + self.T(u.text, mode)) if u.text else ""
                out.append(self.marker("%s name=%s" % (u.id, u.name)) + label + txt)
                first = False
            i += 1
        while links:                       # 닫히지 않은 링크
            u = links.pop()
            if u:
                out.append("](%s)" % u if mode != "html" else "</a>")
        return "".join(out)

    def T_start(self, s, mode):
        """문단 맨 앞 글자: md/head 모드에서 앞머리 구문 문자를 이스케이프"""
        if mode == "cell":
            # 칸 맨 앞 글자가 목록(-,+,숫자.)·제목(#)·굵은 줄(_)로 읽히지 않게 첫 글자만 이스케이프
            body = self.T(s, "cell")
            raw = body[len(S_CELL):] if self.sentinel else body
            if raw[:1] in ("#", "+", "-", "_"):
                raw = "\\" + raw
            else:
                m = re.match(r"(\d+)([.)])", raw)
                if m:
                    raw = m.group(1) + "\\" + raw[len(m.group(1)):]
            return (S_CELL + raw) if self.sentinel else raw
        if mode not in ("md", "head"):
            return self.T(s, mode)
        s = s.replace("\n", " ")
        body = MD_ESC.sub(r"\\\1", s)
        if body.startswith(("#", ">")) or re.match(r"[+\-] ", body):
            body = "\\" + body
        else:
            m = re.match(r"(\d+)([.)])", body)
            if m:
                body = m.group(1) + "\\" + body[len(m.group(1)):]
        return (S_MD + body + S_END) if self.sentinel else body
    def eq_str(self, eq, mode):
        res = eq.res
        mk = self.marker(eq.id)
        if res is None or res["status"] == "원문보존" or not res["latex"]:
            raw = re.sub(r"\s+", " ", eq.script).strip()
            if mode == "html":
                return mk + "<code>[수식 원문] %s</code>" % html.escape(raw, quote=False)
            fence = "`" * (max([len(m) for m in re.findall(r"`+", raw)] + [0]) + 1)
            return mk + "%s[수식 원문] %s%s" % (fence, raw, fence)
        tex = res["latex"]
        if mode == "pipe":
            tex = re.sub(r"(?<!\\)\|", r"\\vert ", tex)
        if mode == "html":
            tex = html.escape(tex, quote=False)
        return mk + "$" + tex + "$"

    def pic_str(self, pic, mode):
        self.stats["images"] += 1
        mk = self.marker(pic.id)
        if not pic.src:
            self.stats["image_missing"] += 1
            return mk + "[그림 파일 없음]"
        rel = image_rel(pic.src)
        if mode == "html":
            return mk + '<img src="%s" alt="그림">' % rel
        return mk + "![그림](%s)" % rel

    # ---------------------------------------------------------- 블록
    def blocks_to_chunks(self, blocks):
        chunks = []
        for b in blocks:
            if isinstance(b, Para):
                chunks += self.para_chunks(b)
            elif isinstance(b, Table):
                chunks += self.table_chunks(b)
            elif isinstance(b, Box):
                chunks += self.box_chunks(b)
        return chunks

    def para_chunks(self, p):
        # 수식만 있는 문단(표 밖) -> 독립 수식
        items = [it for it in p.items if not (it[0] == "text" and not it[1].strip())]
        out = []
        if p.ctx == "body" and items and all(it[0] == "eq" for it in items):
            for it in items:
                eq = it[1]
                res = eq.res
                if res and res["status"] != "원문보존" and res["latex"]:
                    out.append(("p", self.marker(eq.id) + "$$\n" + res["latex"] + "\n$$"))
                else:
                    out.append(("p", self.eq_str(eq, "md")))
            return out
        pid = id(p)
        if pid in self.heading_of:
            text = self.inline(p.items, "head").strip()
            if text:
                lv = self.heading_of[pid]
                self.stats["headings"].append((lv, self.plain_len(p)))
                return [("h", "#" * min(lv, 6) + " " + text)]
            return []
        if pid in self.list_level:
            text = self.inline(p.items, "md", strip_list_symbol=True).strip()
            if text:
                self.stats["list_items"] += 1
                return [("li", "  " * self.list_level[pid] + "- " + text)]
            return []
        mode = "md"
        text = self.inline(p.items, mode).strip()
        if not text:
            return []
        out.append(("p", text))
        for it in p.items:
            if it[0] == "pic" and it[1].caption:
                out += self.caption_chunks(it[1].caption)
        return out

    def plain_len(self, p):
        return len("".join(it[1] for it in p.items if it[0] == "text").strip())

    def caption_chunks(self, paras):
        out = []
        for cp in paras:
            if not isinstance(cp, Para):
                continue
            text = self.inline(cp.items, "md").strip()
            if text:
                out.append(("p", "**" + text + "**"))
        return out

    def box_chunks(self, box):
        out = []
        for ch in self.blocks_to_chunks(box.blocks):
            lines = ch[1].split("\n")
            tag = "[글상자] " if box.name != "caption" else ""
            out.append(("q", "\n".join("> " + (tag if i == 0 else "") + ln for i, ln in enumerate(lines))))
        return out

    # ---------------------------------------------------------- 표
    def cell_text(self, cell, mode):
        parts = []
        for b in cell.blocks:
            if isinstance(b, Para):
                # 수식 칸(cell 모드)은 내용 맨 앞 글자만 목록·제목 기호 이스케이프를 받는다
                t = self.inline(b.items, mode, line_start=(mode == "cell" and not parts)).strip()
                if t:
                    parts.append(t)
            elif isinstance(b, Table):
                parts.append(self.table_html(b))
            elif isinstance(b, Box):
                for ch in self.blocks_to_chunks(b.blocks):
                    parts.append(ch[1].replace("\n", " "))
        return "<br>".join(parts)

    @staticmethod
    def grid_rows(tbl):
        rows = {}
        for c in tbl.cells:
            rows.setdefault(c.row, []).append(c)
        return [sorted(rows[r], key=lambda c: c.col) for r in sorted(rows)]

    def pipe_ok(self, tbl):
        if not tbl.cells:
            return False
        rows = self.grid_rows(tbl)
        ncol = len(rows[0])
        for row in rows:
            if len(row) != ncol:
                return False
        for c in tbl.cells:
            if c.rs != 1 or c.cs != 1:
                return False
            real = [b for b in c.blocks if not (isinstance(b, Para) and not any(
                (it[0] != "text") or it[1].strip() for it in b.items))]
            if len(real) > 1:
                return False
            for b in real:
                if not isinstance(b, Para):
                    return False
                if any(it[0] == "pic" for it in b.items):
                    return False
        return True

    def cell_has_math(self, cell):
        """칸 안(글상자 포함, 중첩 표는 제외)에 LaTeX로 변환된 수식이 있으면 True"""
        def scan(blocks):
            for b in blocks:
                if isinstance(b, Para):
                    for it in b.items:
                        if it[0] == "eq" and it[1].res and it[1].res["status"] != "원문보존" and it[1].res["latex"]:
                            return True
                elif isinstance(b, Box) and scan(b.blocks):
                    return True
            return False
        return scan(cell.blocks)

    def guard_cell_start(self, inner):
        """수식 칸 내용이 마크다운 구문(인용 >, 점검용 주석 줄)으로 시작하면 막는다"""
        if inner.startswith(">"):
            inner = "\\" + inner               # 글상자의 '> ' 줄머리가 인용문으로 읽히지 않게
        if inner.startswith("<!--"):
            inner = "<span></span>" + inner    # 주석 줄은 HTML 블록이라 뒤의 $...$ 가 처리되지 않으므로 앞에 빈 태그
        return inner

    def table_html(self, tbl):
        rows = self.grid_rows(tbl)
        any_header = any(c.header for c in tbl.cells)
        lines = [self.marker(tbl.id) + "<table>"]
        for ri, row in enumerate(rows):
            tds = []
            for c in row:
                tag = "th" if (c.header or (not any_header and ri == 0)) else "td"
                attr = ""
                if c.cs > 1:
                    attr += ' colspan="%d"' % c.cs
                if c.rs > 1:
                    attr += ' rowspan="%d"' % c.rs
                if self.cell_has_math(c):
                    # 수식($...$)이 있는 칸: 앞뒤를 빈 줄로 둘러싸 일반 문단으로 처리되게 한다
                    inner = self.cell_text(c, "cell")
                    inner = self.guard_cell_start(inner)
                    tds.append("<%s%s>\n\n%s\n\n</%s>" % (tag, attr, inner, tag))
                else:
                    tds.append("<%s%s>%s</%s>" % (tag, attr, self.cell_text(c, "html"), tag))
            lines.append("<tr>" + "".join(tds) + "</tr>")
        lines.append("</table>")
        return "\n".join(lines)

    def table_chunks(self, tbl):
        out = []
        cap = self.caption_chunks(tbl.caption)
        if tbl.caption_side == "TOP":
            out += cap
        if self.pipe_ok(tbl):
            rows = self.grid_rows(tbl)
            lines = []
            for ri, row in enumerate(rows):
                lines.append("| " + " | ".join(self.cell_text(c, "pipe") for c in row) + " |")
                if ri == 0:
                    lines.append("| " + " | ".join("---" for _ in row) + " |")
            body = "\n".join(lines)
            out.append(("tbl", self.marker(tbl.id) + "\n" + body if self.markers else body))
        else:
            body = self.table_html(tbl)
            out.append(("tbl", body))
        if tbl.caption_side != "TOP":
            out += cap
        return out

    # ---------------------------------------------------------- 전체
    def render(self):
        doc = self.doc
        chunks = self.blocks_to_chunks(doc.blocks)
        # 각주 정의
        notes = []
        for idx, foot in enumerate(doc.footnotes, 1):
            texts = []
            for b in foot.blocks:
                if isinstance(b, Para):
                    t = self.inline(b.items, "md", line_start=False).strip()
                    if t:
                        texts.append(t)
            notes.append(("note", "[^%d]: %s%s" % (idx, self.marker(foot.id), " ".join(texts))))
        if notes:
            chunks.append(("p", "---"))
            chunks += notes
        # 머리말/꼬리말 부록
        hf_lines = []
        for label, blocks in doc.hf:
            for b in blocks:
                if isinstance(b, Para):
                    t = self.inline(b.items, "md", line_start=False).strip()
                    if t:
                        hf_lines.append(("li", "- [%s] %s" % (label, t)))
        if hf_lines:
            chunks.append(("h", "## 부록: 머리말·꼬리말"))
            chunks += hf_lines
        # 목록 묶음(연속한 li)의 맨 왼쪽 들여쓰기를 0으로 맞춘다. 수식 블록($$)·표·본문 문단 뒤에서 새로 시작하는 목록이
        # 4칸 이상 들여쓰이면 마크다운이 코드 블록으로 읽어 안의 $...$ 수식이 그대로 보이기 때문이다(2011년판에서 확인).
        fixed, i = [], 0
        while i < len(chunks):
            if chunks[i][0] != "li":
                fixed.append(chunks[i])
                i += 1
                continue
            j = i
            while j < len(chunks) and chunks[j][0] == "li":
                j += 1
            run = chunks[i:j]
            base = min(len(t) - len(t.lstrip(" ")) for _, t in run)
            fixed += [(k, t[base:]) for k, t in run]
            i = j
        chunks = fixed
        # 조립: 목록/인용/각주는 줄바꿈 하나, 나머지는 빈 줄
        out = []
        prev = None
        for kind, text in chunks:
            if prev is None:
                sep = ""
            elif kind == prev and kind in ("li", "q", "note"):
                sep = "\n"
            else:
                sep = "\n\n"
            out.append(sep + text)
            prev = kind
        return "".join(out) + "\n"


def strip_sentinels(s):
    return re.sub("[]", "", s)


def extract_t_text(annotated):
    """표지로 감싼 글자만 모아 이스케이프를 되돌린 원문 글자열 목록을 돌려준다."""
    parts = []
    for m in re.finditer("([])(.*?)", annotated, re.S):
        body = m.group(2)
        if m.group(1) == "":
            parts.append(html.unescape(body))
        elif m.group(1) == "":
            parts.append(html.unescape(unescape_md(body)))   # 칸 모드: 마크다운 이스케이프 먼저, 그 다음 HTML 엔티티
        else:
            parts.append(unescape_md(body))
    return parts


