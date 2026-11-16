"""hwpx -> IR(문서 순서 목록). 문서 숫자나 파일 이름을 코드에 넣지 않는다.
zip은 풀지 않고 zipfile로 메모리에서 읽는다(원본 무변경).
"""
import collections
import os
import re
import sys
import zipfile
from xml.etree import ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hwpx_common import HC, HH, HP, load_classification, read_spine, split_tag  # noqa: E402

T = "{%s}t" % HP


# ---------------------------------------------------------------- IR 클래스
_HANGUL = "가나다라마바사아자차카타파하"
_CIRCLED_HANGUL = "㉮㉯㉰㉱㉲㉳㉴㉵㉶㉷㉸㉹㉺㉻"
_JAMO = "ㄱㄴㄷㄹㅁㅂㅅㅇㅈㅊㅋㅌㅍㅎ"
_ROMAN = ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"),
          (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"))


def format_number(n, fmt):
    """번호 n 을 번호 형식(hwpx numFormat)대로 글자로 바꾼다. 범위를 벗어나거나 모르는 형식이면 숫자 그대로."""
    if fmt == "CIRCLED_DIGIT" and 1 <= n <= 20:
        return chr(0x2460 + n - 1)
    if fmt in ("ROMAN_CAPITAL", "ROMAN_SMALL") and 1 <= n <= 3999:
        out, m = "", n
        for v, sym in _ROMAN:
            while m >= v:
                out, m = out + sym, m - v
        return out if fmt == "ROMAN_CAPITAL" else out.lower()
    if fmt == "LATIN_CAPITAL" and 1 <= n <= 26:
        return chr(64 + n)
    if fmt == "LATIN_SMALL" and 1 <= n <= 26:
        return chr(96 + n)
    for name, table in (("HANGUL_SYLLABLE", _HANGUL), ("CIRCLED_HANGUL_SYLLABLE", _CIRCLED_HANGUL), ("HANGUL_JAMO", _JAMO)):
        if fmt == name and 1 <= n <= len(table):
            return table[n - 1]
    return str(n)


class Para:
    def __init__(self, ctx, ppr, style, cont=False):
        self.ctx, self.ppr, self.style, self.cont = ctx, ppr, style, cont
        self.items = []     # ('text',s) ('lb',) ('tab',) ('eq',Eq) ('pic',Pic) ('auto',s) ('fn',Foot) ('fb',kind,url) ('fe',) ('uns',Uns)
        self.pending = []   # 이 문단 뒤에 붙일 블록(그림 캡션 등)


class Eq:
    def __init__(self, eid, script, comment, loc):
        self.id, self.script, self.comment, self.loc = eid, script, comment, loc
        self.res = None


class Pic:
    def __init__(self, pid, ref, src, comment):
        self.id, self.ref, self.src, self.comment = pid, ref, src, comment
        self.caption = []


class Foot:
    def __init__(self, fid, number, kind):
        self.id, self.number, self.kind = fid, number, kind
        self.blocks = []


class Uns:
    def __init__(self, uid, name, text, count, path):
        self.id, self.name, self.text, self.count, self.path = uid, name, text, count, path


class Cell:
    def __init__(self, row, col, rs, cs, header):
        self.row, self.col, self.rs, self.cs, self.header = row, col, rs, cs, header
        self.blocks = []


class Table:
    def __init__(self, tid, rows, cols):
        self.id, self.rows, self.cols = tid, rows, cols
        self.cells = []
        self.caption = []
        self.caption_side = "TOP"


class Box:
    def __init__(self, bid, name):
        self.id, self.name = bid, name
        self.blocks = []


class HwpxDoc:
    def __init__(self, path, key):
        self.path, self.key = path, key
        self.classes = load_classification()
        self.z = zipfile.ZipFile(path)
        self.sections, self.manifest = read_spine(self.z)
        self.roots = []                  # (part, root)
        self.t_all = []                  # 문서 순서 모든 t 요소
        self.consumed = collections.Counter()
        self.blocks = []                 # 본문 블록
        self.footnotes = []              # Foot 목록 (각주/미주)
        self.hf = []                     # (종류, [블록])  머리말/꼬리말/마스터페이지
        self.eqs, self.tables, self.pics, self.unsup = [], [], [], []
        self.counters = collections.defaultdict(int)
        self.reset_flag = collections.defaultdict(bool)
        self.autonum = {"total": 0, "rendered": 0, "suppressed": 0, "skips": [], "nonDigit": 0, "unknown": []}
        self.fields = collections.Counter()
        self.warnings = []
        self.shape_comments = []
        self.seq = collections.Counter()
        self._load()

    # ---- 읽기 ------------------------------------------------------------
    def _load(self):
        names = set(self.z.namelist())
        for part in self.sections:
            self.roots.append((part, ET.fromstring(self.z.read(part))))
        for part in sorted(n for n in names if re.match(r"Contents/masterpage\d+\.xml$", n)):
            self.roots.append((part, ET.fromstring(self.z.read(part))))
        for part, root in self.roots:
            self.t_all += list(root.iter(T))
        # 개수를 알아 id 자릿수를 정한다
        cnt = collections.Counter()
        for part, root in self.roots:
            for el in root.iter():
                cnt[split_tag(el.tag)[1]] += 1
        self.counts = cnt
        self.width = {k: max(4, len(str(cnt.get(n, 0)))) for k, n in
                      (("EQ", "equation"), ("TB", "tbl"), ("PIC", "pic"), ("FN", "footNote"))}
        self.width["UNS"] = 4
        self._load_header()
        for part, root in self.roots:
            if "masterpage" in part:
                self._read_masterpage(part, root)
            else:
                self._read_section(root)

    def _load_header(self):
        """header.xml에서 문단 모양(개요 수준, 왼쪽 여백)과 스타일 이름을 읽는다."""
        self.para_pr, self.styles = {}, {}
        self.numberings, self.bullets = {}, {}   # 문단 번호 정의 {id: {수준: (형식문자열, 번호형식, 시작값)}}, 글머리표 {id: 글자}
        self.num_counter = {}                    # {번호 정의 id: [수준별 현재 번호]}  (문서 순서대로 올라간다)
        self.num_labels = collections.Counter()   # 넣은 번호 개수(종류별)
        self.num_chars = collections.Counter()    # 넣은 번호 글자(공백 제외). 점검 C1 이 '원본에 없는 글자'로 오해하지 않게 기록한다
        if "Contents/header.xml" not in self.z.namelist():
            return
        root = ET.fromstring(self.z.read("Contents/header.xml"))
        for el in root.iter():
            uri, name = split_tag(el.tag)
            if name == "paraPr":
                heading = ("NONE", 0)
                ref = None
                left = None
                for sub in el.iter():
                    sn = split_tag(sub.tag)[1]
                    if sn == "heading":
                        heading = (sub.get("type"), int(sub.get("level", "0")))
                        ref = sub.get("idRef")
                    elif sn == "left" and left is None:
                        left = int(sub.get("value", "0"))
                self.para_pr[el.get("id")] = {"heading": heading, "left": left or 0, "ref": ref}
            elif name == "style":
                self.styles[el.get("id")] = el.get("name", "")
            elif name == "numbering":
                levels = {}
                for ph in el:
                    if split_tag(ph.tag)[1] == "paraHead":
                        levels[int(ph.get("level", "1"))] = (
                            "".join(ph.itertext()), ph.get("numFormat", "DIGIT"), int(ph.get("start", "1")))
                self.numberings[el.get("id")] = levels
            elif name == "bullet":
                self.bullets[el.get("id")] = el.get("char", "")

    # ---- 문단 번호(한글이 글자가 아니라 번호 기능으로 그려 주는 번호) ----------------
    def number_label(self, ppr):
        """paraPr 의 heading 이 NUMBER 이면 번호 글자("<그림 2-2>" 같은 것)를, BULLET 이면 글머리표 글자를 돌려준다.
        같은 번호 정의(idRef)를 쓰는 문단끼리 수준별로 센다: 같은 수준이면 +1, 더 깊은 수준은 처음으로."""
        pp = self.para_pr.get(ppr)
        if not pp:
            return ""
        kind, depth = pp["heading"]
        ref = pp.get("ref")
        if kind == "BULLET":
            ch = self.bullets.get(ref, "")
            return ch if ch else ""
        if kind != "NUMBER" or ref is None:
            return ""
        levels = self.numberings.get(ref)
        if not levels:
            return ""
        level = depth + 1
        cur = self.num_counter.setdefault(ref, [])
        del cur[level:]
        while len(cur) < level:
            cur.append(None)
        start = levels.get(level, ("", "DIGIT", 1))[2]
        cur[depth] = start if cur[depth] is None else cur[depth] + 1
        nums = [1 if n is None else n for n in cur]
        template = levels.get(level, ("^%d." % level, "DIGIT", 1))[0]

        def rep(m):
            tok = m.group(1)
            k = (len(nums) if tok in "nN" else int(tok)) - 1
            n = nums[k] if 0 <= k < len(nums) else 1
            fmt = levels.get(k + 1, ("", "DIGIT", 1))[1]
            return format_number(n, fmt)
        return re.sub(r"\^([1-7nN])", rep, template).strip()

    def nid(self, kind):
        self.seq[kind] += 1
        return "%s%0*d" % (kind, self.width.get(kind, 4), self.seq[kind])

    def cls(self, el):
        uri, name = split_tag(el.tag)
        return self.classes.get((uri, name), ("미분류", ""))[0], name

    # ---- t 텍스트 -----------------------------------------------------------
    def t_text(self, t):
        """t 요소의 글자(공백류 개체 포함)를 읽고 소비로 기록한다. 반환: 항목 목록"""
        self.consumed[id(t)] += 1
        items = []
        if t.text:
            items.append(("text", t.text))
        for ch in t:
            uri, name = split_tag(ch.tag)
            if name == "tab":
                items.append(("tab",))
            elif name == "lineBreak":
                items.append(("lb",))
            elif name in ("fwSpace", "nbSpace"):
                items.append(("text", " "))
            elif name == "titleMark":
                pass
            else:
                c, _ = self.cls(ch)
                if c in ("서식무시", "설명문구"):
                    pass
                else:
                    items.append(("uns", self._make_uns(ch)))
            if ch.tail:
                items.append(("text", ch.tail))
        return items

    def _make_uns(self, el):
        texts = []
        for t in el.iter(T):
            for it in self.t_text(t):
                if it[0] == "text":
                    texts.append(it[1])
        uri, name = split_tag(el.tag)
        u = Uns(self.nid("UNS"), name, "".join(texts), sum(1 for _ in el.iter()), name)
        self.unsup.append(u)
        return u

    # ---- 섹션/문단 ---------------------------------------------------------
    def _read_section(self, root):
        self.blocks += self.read_paras(root, "body")

    def _read_masterpage(self, part, root):
        texts = []
        for t in root.iter(T):
            texts += [it[1] for it in self.t_text(t) if it[0] == "text"]
        if "".join(texts).strip():
            p = Para("hf", None, None)
            p.items = [("text", "".join(texts))]
            self.hf.append(("마스터페이지", [p]))

    def read_paras(self, container, ctx):
        """container의 자식 p를 문서 순서대로 읽어 블록 목록으로"""
        out = []
        for ch in container:
            uri, name = split_tag(ch.tag)
            if name == "p":
                self.read_p(ch, ctx, out)
            elif name in ("subList",):
                out += self.read_paras(ch, ctx)
            else:
                c, _ = self.cls(ch)
                if c in ("서식무시", "설명문구"):
                    continue
                pb = _Builder(self, ctx, None, None, out)
                self.inline(ch, pb)
                pb.finish()
        return out

    def read_p(self, p, ctx, out):
        start = len(out)
        pb = _Builder(self, ctx, p.get("paraPrIDRef"), p.get("styleIDRef"), out)
        for ch in p:
            uri, name = split_tag(ch.tag)
            if name == "run":
                for c in ch:
                    self.inline(c, pb)
            else:
                self.inline(ch, pb)
        pb.finish()
        self.add_number_label(p.get("paraPrIDRef"), out[start:])

    def add_number_label(self, ppr, new_blocks):
        """한글이 번호 기능으로 그려 주는 번호·글머리표를 문단 맨 앞 글자로 넣는다(글자가 있는 문단만)."""
        paras = [b for b in new_blocks if isinstance(b, Para)]
        if not paras or not any(it[0] == "text" and it[1].strip() for b in paras for it in b.items):
            return
        label = self.number_label(ppr)
        if not label:
            return
        first = next((b for b in paras if b.items), None)
        if first is None:
            return
        kind = self.para_pr[ppr]["heading"][0]
        label = "".join("•" if 0xE000 <= ord(c) <= 0xF8FF else c for c in label)   # 글꼴 전용 글자(PUA)는 일반 글머리표로
        first.items.insert(0, ("text", label + " "))
        self.num_labels[kind] += 1
        self.num_chars.update("".join(label.split()))

    # ---- run/ctrl 자식 처리 -------------------------------------------------------
    def inline(self, el, pb):
        uri, name = split_tag(el.tag)
        cls, _ = self.cls(el)
        if name == "t":
            pb.cur.items += self.t_text(el)
        elif name == "ctrl":
            for c in el:
                self.inline(c, pb)
        elif name == "equation":
            pb.cur.items.append(("eq", self.make_eq(el)))
        elif name == "pic":
            self.make_pic(el, pb)
        elif name == "tbl":
            pb.block(self.make_table(el))
        elif name in ("rect", "container") or (name == "drawText"):
            self.make_shape(el, pb)
        elif name in ("footNote", "endNote"):
            pb.cur.items.append(("fn", self.make_note(el, name)))
        elif name == "autoNum":
            self.do_autonum(el, pb.cur)
        elif name == "newNum":
            nt = el.get("numType")
            if nt and nt != "PAGE":
                self.counters[nt] = int(el.get("num", "1")) - 1
                self.reset_flag[nt] = True
        elif name == "fieldBegin":
            ftype = el.get("type", "")
            self.fields[ftype] += 1
            url = ""
            if ftype == "HYPERLINK":
                for sp in el.iter("{%s}stringParam" % HP):
                    if sp.get("name") in ("Command", None) or not url:
                        url = (sp.text or "").split(";")[0].replace("\\:", ":").strip()
                        if sp.get("name") == "Command":
                            break
            pb.cur.items.append(("fb", ftype, url))
        elif name == "fieldEnd":
            pb.cur.items.append(("fe",))
        elif name in ("header", "footer"):
            sub = el.find("{%s}subList" % HP)
            blocks = self.read_paras(sub, "hf") if sub is not None else []
            self.hf.append(("머리말" if name == "header" else "꼬리말", blocks))
        elif name in ("parameters", "stringParam", "cellAddr", "cellSpan", "script", "img", "caption", "subList", "tr", "tc"):
            pass   # 상위 처리기가 읽는 요소(문맥 밖에서 만나면 무시하되 t가 있으면 C2가 잡는다)
        elif name == "p":
            self.read_p(el, pb.ctx, pb.out)
        elif cls in ("서식무시", "설명문구"):
            if name == "shapeComment" and el.text:
                self.shape_comments.append(el.text)
        else:
            pb.cur.items.append(("uns", self._make_uns(el)))

    # ---- 개체별 ------------------------------------------------------------
    def make_eq(self, el):
        script = comment = None
        for ch in el:
            n = split_tag(ch.tag)[1]
            if n == "script":
                script = ch.text or ""
            elif n == "shapeComment":
                comment = ch.text or ""
                self.shape_comments.append(comment)
        eq = Eq(self.nid("EQ"), script or "", comment, "")
        self.eqs.append(eq)
        return eq

    def make_pic(self, el, pb):
        ref = comment = None
        caption = None
        for ch in el:
            n = split_tag(ch.tag)[1]
            if n == "img":
                ref = ch.get("binaryItemIDRef")
            elif n == "shapeComment":
                comment = ch.text or ""
                self.shape_comments.append(comment)
            elif n == "caption":
                caption = ch
        src = None
        if ref is not None:
            href = self.manifest.get(ref, (None,))[0]
            if href and href in self.z.namelist():
                src = href
            else:
                for nm in self.z.namelist():
                    if nm.startswith("BinData/") and os.path.splitext(os.path.basename(nm))[0] == ref:
                        src = nm
                        break
        pic = Pic(self.nid("PIC"), ref, src, comment)
        if caption is not None:
            sub = caption.find("{%s}subList" % HP)
            if sub is not None:
                pic.caption = self.read_paras(sub, "caption")
        self.pics.append(pic)
        pb.cur.items.append(("pic", pic))
        # 그림 안에 글상자 글자가 있을 수 있으므로 다른 자식도 처리
        for ch in el:
            n = split_tag(ch.tag)[1]
            if n in ("drawText", "rect", "container"):
                self.make_shape(ch, pb)

    def make_shape(self, el, pb):
        uri, name = split_tag(el.tag)
        if name == "container":
            for ch in el:
                c, n = self.cls(ch)
                if c in ("서식무시", "설명문구"):
                    if n == "shapeComment" and ch.text:
                        self.shape_comments.append(ch.text)
                    continue
                self.inline(ch, pb)
            return
        # rect / drawText: 안의 문단을 글상자로
        blocks = []
        dt = el if name == "drawText" else el.find("{%s}drawText" % HP)
        inner_nodes = []
        for ch in el:
            c, n = self.cls(ch)
            if n == "drawText":
                sub = ch.find("{%s}subList" % HP)
                if sub is not None:
                    blocks += self.read_paras(sub, "box")
            elif n == "shapeComment":
                if ch.text:
                    self.shape_comments.append(ch.text)
            elif n in ("subList",) and name == "drawText":
                blocks += self.read_paras(ch, "box")
            elif c in ("서식무시",):
                continue
            elif n in ("rect", "pic", "container", "line", "caption"):
                inner_nodes.append(ch)
            else:
                inner_nodes.append(ch)
        box = Box(self.nid("BX"), name)
        box.blocks = blocks
        if blocks:
            pb.block(box)
        for ch in inner_nodes:
            if split_tag(ch.tag)[1] == "caption":
                sub = ch.find("{%s}subList" % HP)
                if sub is not None:
                    pb.block(Box(self.nid("BX"), "caption"))
                    pb.out[-1].blocks = self.read_paras(sub, "caption")
                continue
            self.inline(ch, pb)

    def make_note(self, el, name):
        number = el.get("number")
        foot = Foot(self.nid("FN"), number, "각주" if name == "footNote" else "미주")
        sub = el.find("{%s}subList" % HP)
        if sub is not None:
            foot.blocks = self.read_paras(sub, "note")
        self.footnotes.append(foot)
        if foot.number is None:
            foot.number = str(len(self.footnotes))
        return foot

    def do_autonum(self, el, para):
        nt = el.get("numType", "")
        self.autonum["total"] += 1
        if nt in ("FOOTNOTE", "ENDNOTE", "PAGE"):
            self.autonum["suppressed"] += 1      # 각주 본문의 번호는 [^n]이 대신함
            return
        if nt not in ("TABLE", "PICTURE", "EQUATION", "FIGURE"):
            self.autonum["unknown"].append(nt)
            para.items.append(("auto", "[번호?]"))
            return
        fmt = el.find("{%s}autoNumFormat" % HP)
        if fmt is not None and fmt.get("type", "DIGIT") != "DIGIT":
            self.autonum["nonDigit"] += 1
        if el.get("num") is not None:
            n = int(el.get("num"))
        else:
            n = self.counters[nt] + 1
        prev = self.counters.get(nt, 0)
        if self.seq["_auto_" + nt] and n != prev + 1 and not self.reset_flag[nt]:
            self.autonum["skips"].append((nt, prev, n))
        self.seq["_auto_" + nt] += 1
        self.counters[nt] = n
        self.reset_flag[nt] = False
        self.autonum["rendered"] += 1
        para.items.append(("auto", str(n)))

    def make_table(self, el):
        tbl = Table(self.nid("TB"), int(el.get("rowCnt", "0") or 0), int(el.get("colCnt", "0") or 0))
        self.tables.append(tbl)
        for ch in el:
            n = split_tag(ch.tag)[1]
            if n == "caption":
                tbl.caption_side = ch.get("side", "TOP")
                sub = ch.find("{%s}subList" % HP)
                if sub is not None:
                    tbl.caption = self.read_paras(sub, "caption")
            elif n == "tr":
                for tc in ch:
                    if split_tag(tc.tag)[1] != "tc":
                        continue
                    addr = tc.find("{%s}cellAddr" % HP)
                    span = tc.find("{%s}cellSpan" % HP)
                    cell = Cell(int(addr.get("rowAddr", "0")) if addr is not None else 0,
                                int(addr.get("colAddr", "0")) if addr is not None else 0,
                                int(span.get("rowSpan", "1")) if span is not None else 1,
                                int(span.get("colSpan", "1")) if span is not None else 1,
                                tc.get("header") == "1")
                    sub = tc.find("{%s}subList" % HP)
                    if sub is not None:
                        cell.blocks = self.read_paras(sub, "cell")
                    tbl.cells.append(cell)
        return tbl

    def unconsumed_t(self):
        return [t for t in self.t_all if self.consumed.get(id(t), 0) == 0]

    def multi_consumed_t(self):
        return [t for t in self.t_all if self.consumed.get(id(t), 0) > 1]


class _Builder:
    """한 문단을 만들면서 표/글상자 같은 블록 개체를 만나면 문단을 끊어 순서를 지킨다."""

    def __init__(self, doc, ctx, ppr, style, out):
        self.doc, self.ctx, self.ppr, self.style, self.out = doc, ctx, ppr, style, out
        self.cur = Para(ctx, ppr, style)

    def block(self, blk):
        if self.cur.items:
            self.out.append(self.cur)
        self.out.append(blk)
        self.cur = Para(self.ctx, self.ppr, self.style, cont=True)

    def finish(self):
        if self.cur.items:
            self.out.append(self.cur)


