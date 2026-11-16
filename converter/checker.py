"""문서별 자동 점검 (C1~C21). 모든 기준은 그 문서의 XML에서 센 값 대 md에서 센 값이다. 고정 숫자 없음."""
import collections
import html
import html.parser
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hwpeq2latex import DICTIONARY, RULES_EXACT, check_latex  # noqa: E402
# 규칙표에서 '자기 자신'으로 바꾸도록 정한 단어(MOD, IZD 등 변수로 읽기로 한 것)는 C11에서 명령 잔존으로 보지 않는다
SELF_VARS = {k for k, (kind, tex) in RULES_EXACT.items() if kind == "기호" and tex == k}
from hwpx_common import HP, split_tag  # noqa: E402
from hwpx_reader import T  # noqa: E402
from md_writer import LIST_SYMBOLS, extract_t_text, strip_sentinels  # noqa: E402

PASS, WARN, FAIL, INFO, NA = "합격", "경고", "실패", "참고", "해당없음"
WORST = {PASS: 0, NA: 0, INFO: 0, WARN: 1, FAIL: 2}


def ws_free(s):
    return re.sub(r"\s+", "", s)


def letters_only(s):
    return "".join(ch for ch in s if ch.isalpha())


def row(cid, name, verdict, value, note=""):
    return {"id": cid, "name": name, "verdict": verdict, "value": value, "note": note}


# --------------------------------------------------------------- md 표 파서
class TableParser(html.parser.HTMLParser):
    """점검용 md의 HTML 표에서 칸 주소를 계산한다. 마커 주석(TB/EQ/PIC)의 위치도 기록."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []            # 열린 표
        self.pending_tb = None
        self.eq_loc = {}           # EQ id -> (TB id, row, col) 또는 None
        self.eq_count = collections.Counter()
        self.cells = collections.defaultdict(list)   # TB id -> [(row, col, text, flags)]
        self.cur_cell = None
        self.cell_stack = []

    def handle_comment(self, data):
        m = re.match(r"(TB|EQ|PIC)\d+", data)
        if not m:
            return
        key = data.split()[0]
        if m.group(1) == "TB":
            self.pending_tb = key
        elif m.group(1) == "EQ":
            self.eq_count[key] += 1
            self.eq_loc[key] = self._loc()
            self._mark_cell("marker")
        else:
            self._mark_cell("marker")

    def _loc(self):
        if self.stack and self.stack[-1].get("cell"):
            t = self.stack[-1]
            return (t["id"], t["row"], t["cell"]["col"])
        return None

    def _mark_cell(self, flag):
        if self.stack and self.stack[-1].get("cell"):
            self.stack[-1]["cell"]["flags"].add(flag)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table":
            self._mark_cell("table")
            self.stack.append({"id": self.pending_tb, "row": -1, "occ": set(), "col": 0, "cell": None})
            self.pending_tb = None
        elif tag == "tr" and self.stack:
            t = self.stack[-1]
            t["row"] += 1
            t["col"] = 0
        elif tag in ("td", "th") and self.stack:
            t = self.stack[-1]
            while (t["row"], t["col"]) in t["occ"]:
                t["col"] += 1
            cs, rs = int(a.get("colspan", 1)), int(a.get("rowspan", 1))
            for rr in range(t["row"], t["row"] + rs):
                for cc in range(t["col"], t["col"] + cs):
                    t["occ"].add((rr, cc))
            t["cell"] = {"col": t["col"], "text": [], "flags": set()}
            t["col"] += cs
        elif tag in ("img",):
            self._mark_cell("marker")

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.stack and self.stack[-1].get("cell"):
            t = self.stack[-1]
            c = t["cell"]
            self.cells[t["id"]].append((t["row"], c["col"], "".join(c["text"]), c["flags"]))
            t["cell"] = None
        elif tag == "table" and self.stack:
            self.stack.pop()

    def handle_data(self, data):
        if self.stack and self.stack[-1].get("cell"):
            self.stack[-1]["cell"]["text"].append(data)


def split_pipe_row(line):
    s = line.strip()
    s = s[1:-1] if s.endswith("|") else s[1:]
    return re.split(r"(?<!\\)\|", s)


def parse_md_tables(text):
    """점검용 md(또는 hwp2md md)에서 파이프 표와 HTML 표를 모두 읽는다.
    반환: (parser, pipe_cells) pipe_cells: TB id(또는 임의 키) -> [(row, col, text, flags)]"""
    lines = text.split("\n")
    pipe_cells = collections.defaultdict(list)
    keep = []
    i = 0
    pipe_eq_loc = {}
    pipe_eq_count = collections.Counter()
    pipe_index = 0
    while i < len(lines):
        ln = lines[i]
        m = re.match(r"^<!--(TB\d+)-->$", ln)
        is_pipe_start = ln.startswith("|") and i + 1 < len(lines) and re.match(r"^\|\s*-{3}", lines[i + 1] or "")
        if (m and i + 1 < len(lines) and lines[i + 1].startswith("|")) or is_pipe_start:
            if m:
                tb = m.group(1)
                i += 1
            else:
                tb = "PIPE%06d" % pipe_index
            pipe_index += 1
            r = 0
            idx = 0
            while i < len(lines) and lines[i].startswith("|"):
                if idx != 1:
                    for c, cell in enumerate(split_pipe_row(lines[i])):
                        flags = set()
                        for eqm in re.finditer(r"<!--(EQ\d+)-->", cell):
                            pipe_eq_loc[eqm.group(1)] = (tb, r, c)
                            pipe_eq_count[eqm.group(1)] += 1
                            flags.add("marker")
                        if "<!--PIC" in cell or "<img" in cell:
                            flags.add("marker")
                        if "<table" in cell:
                            flags.add("table")
                        txt = re.sub(r"<!--.*?-->", "", cell)
                        pipe_cells[tb].append((r, c, txt, flags))
                    r += 1
                idx += 1
                i += 1
            keep.append("")
            continue
        keep.append(ln)
        i += 1
    p = TableParser()
    p.feed("\n".join(keep))
    p.close()
    for k, v in pipe_eq_loc.items():
        p.eq_loc[k] = v
    for k, v in pipe_eq_count.items():
        p.eq_count[k] += v
    return p, pipe_cells


def count_empty_cells(parser, pipe_cells):
    empty = 0
    total = 0
    per_table = {}
    for tb, cells in list(parser.cells.items()) + list(pipe_cells.items()):
        for (r, c, txt, flags) in cells:
            total += 1
            per_table[tb] = per_table.get(tb, 0) + 1
            if not txt.strip() and not flags:
                empty += 1
    return empty, total, per_table


# --------------------------------------------------------------- XML 쪽 독립 계산
def xml_walk(root):
    """루트를 문서 순서로 돌며 (요소, 조상 이름 목록)을 낸다."""
    stack = [(root, ())]
    # 문서 순서(전위)를 위해 자식을 역순으로 넣는다
    while stack:
        el, anc = stack.pop()
        yield el, anc
        nm = split_tag(el.tag)[1]
        na = anc + (el,)
        for ch in reversed(list(el)):
            stack.append((ch, na))


def xml_independent(doc):
    """표 순번, 수식의 칸 위치, 빈 칸 수, 표 격자 검사를 문서 XML에서 독립적으로 계산"""
    tbl_index = {}
    eq_xml = []
    grid_problems = []
    empty_cells = 0
    total_cells = 0
    tbl_cells = {}
    ntbl = 0
    for part, root in doc.roots:
        for el, anc in xml_walk(root):
            nm = split_tag(el.tag)[1]
            if nm == "tbl":
                ntbl += 1
                tbl_index[id(el)] = ntbl
                rows = int(el.get("rowCnt", "0") or 0)
                cols = int(el.get("colCnt", "0") or 0)
                occ = set()
                overlap = 0
                ncell = 0
                if True:   # 이 표의 칸만 본다(중첩 표의 칸은 그 표에서 따로)
                    for tr in el:
                        if split_tag(tr.tag)[1] != "tr":
                            continue
                        for tc in tr:
                            if split_tag(tc.tag)[1] != "tc":
                                continue
                            ncell += 1
                            a = tc.find("{%s}cellAddr" % HP)
                            s = tc.find("{%s}cellSpan" % HP)
                            r0, c0 = int(a.get("rowAddr")), int(a.get("colAddr"))
                            rs, cs = int(s.get("rowSpan", 1)), int(s.get("colSpan", 1))
                            for rr in range(r0, r0 + rs):
                                for cc in range(c0, c0 + cs):
                                    if (rr, cc) in occ:
                                        overlap += 1
                                    occ.add((rr, cc))
                holes = rows * cols - len(occ)
                outside = sum(1 for (rr, cc) in occ if rr >= rows or cc >= cols)
                tbl_cells[ntbl] = ncell
                if overlap or holes or outside:
                    grid_problems.append((ntbl, rows, cols, overlap, holes, outside))
            elif nm == "equation":
                loc = None
                for a_ in reversed(anc):
                    n2 = split_tag(a_.tag)[1]
                    if n2 == "tc":
                        addr = a_.find("{%s}cellAddr" % HP)
                        # 가장 가까운 tc의 표
                        tb = None
                        for b_ in reversed(anc[:anc.index(a_)]):
                            if split_tag(b_.tag)[1] == "tbl":
                                tb = tbl_index.get(id(b_))
                                break
                        loc = (tb, int(addr.get("rowAddr")), int(addr.get("colAddr")))
                        break
                eq_xml.append(loc)
            elif nm == "tc":
                total_cells += 1
                has = False
                for d in el.iter():
                    dn = split_tag(d.tag)[1]
                    if dn == "t" and ws_free("".join(d.itertext())):
                        has = True
                        break
                    if dn in ("equation", "pic", "tbl", "rect", "container", "ole", "chart"):
                        has = True
                        break
                if not has:
                    empty_cells += 1
    return {"tbl_count": ntbl, "eq_loc": eq_xml, "grid_problems": grid_problems,
            "empty_cells": empty_cells, "total_cells": total_cells, "tbl_cells": tbl_cells}


def t_contexts(doc):
    """각 t의 소속 종류(표 칸/각주/머리말/글상자/캡션/본문)"""
    out = []
    for part, root in doc.roots:
        for el, anc in xml_walk(root):
            if split_tag(el.tag)[1] != "t":
                continue
            names = [split_tag(a.tag)[1] for a in anc]
            if "footNote" in names or "endNote" in names:
                k = "각주"
            elif "header" in names or "footer" in names:
                k = "머리말꼬리말"
            elif "drawText" in names or "rect" in names:
                k = "글상자"
            elif "caption" in names:
                k = "캡션"
            elif "tc" in names:
                k = "표칸"
            elif "masterPage" in names:
                k = "마스터페이지"
            else:
                k = "본문"
            out.append((el, k))
    return out


# --------------------------------------------------------------- 점검 본체
def run_checks(doc, w_clean, annotated, clean_md, marked_md, hwp2md_text, image_dir, extras):
    rows = []
    xi = xml_independent(doc)
    stats = w_clean.stats

    # C1 글자 회계
    exp = collections.Counter(ch for t in doc.t_all for ch in ws_free("".join(t.itertext())))
    got = collections.Counter(ch for part in extract_t_text(annotated) for ch in ws_free(part))
    removed = collections.Counter()
    for ch, n in stats["removed_chars"].items():
        removed[ch] += n
    exp = exp + getattr(doc, "num_chars", collections.Counter())   # 한글이 번호 기능으로 그린 번호 글자(우리가 넣음)
    exp = exp - removed                      # 목록 기호는 규칙상 지운 글자(생성 기록)
    missing = exp - got
    extra = got - exp
    if not missing and not extra:
        rows.append(row("C1", "글자 회계", PASS, {"XML글자": sum(exp.values()), "md글자": sum(got.values()), "차이": 0,
                                              "지운목록기호": dict(removed),
                                              "넣은번호": dict(getattr(doc, "num_labels", {})), "넣은번호글자수": sum(getattr(doc, "num_chars", {}).values())}))
    else:
        rows.append(row("C1", "글자 회계", FAIL, {"XML글자": sum(exp.values()), "md글자": sum(got.values()),
                                              "빠진글자": dict(list(missing.items())[:20]),
                                              "남는글자": dict(list(extra.items())[:20])},
                        "빠진 %d자, 남는 %d자" % (sum(missing.values()), sum(extra.values()))))
    # C2 t 소비
    un = doc.unconsumed_t()
    mu = doc.multi_consumed_t()
    ex = ["".join(t.itertext())[:30] for t in (un + mu)[:5]]
    rows.append(row("C2", "t 소비", PASS if not un and not mu else FAIL,
                    {"전체t": len(doc.t_all), "0회": len(un), "2회이상": len(mu), "예시": ex}))
    # C3 요소 분류 범위, C4 무시 요소 안 글자, C16 미지원 개체
    kinds = collections.Counter()
    ign_with_text = []
    for part, root in doc.roots:
        for el in root.iter():
            uri, name = split_tag(el.tag)
            kinds[(uri, name)] += 1
            c = doc.classes.get((uri, name), ("미분류",))[0]
            if c in ("서식무시", "설명문구") and len(el) > 0:
                if any(ws_free("".join(t.itertext())) for t in el.iter(T)):
                    ign_with_text.append(name)
    unknown = [n for (u, n) in kinds if (u, n) not in doc.classes]
    rows.append(row("C3", "요소 분류 범위", PASS if not unknown else WARN, {"요소종류": len(kinds), "분류표에없음": sorted(unknown)}))
    rows.append(row("C4", "무시 요소 안 글자", PASS if not ign_with_text else FAIL,
                    {"개수": len(ign_with_text), "요소": sorted(set(ign_with_text))}))
    # C5 찌꺼기 문구
    xml_text = "".join("".join(t.itertext()) for t in doc.t_all)
    bad5 = []
    for s in sorted(set(doc.shape_comments)):
        cands = {s} | {ln.strip() for ln in s.split("\n") if len(ln.strip()) >= 3}
        for cs in cands:
            if clean_md.count(cs) > xml_text.count(cs):
                bad5.append((cs[:40], clean_md.count(cs), xml_text.count(cs)))
    rows.append(row("C5", "찌꺼기 문구", PASS if not bad5 else FAIL,
                    {"설명문구종류": len(set(doc.shape_comments)), "초과": bad5[:5]}))
    # C6 표 수
    tb_marks = re.findall(r"<!--(TB\d+)-->", marked_md)
    exp_ids = {"TB%0*d" % (doc.width["TB"], i) for i in range(1, xi["tbl_count"] + 1)}
    dup = [k for k, v in collections.Counter(tb_marks).items() if v != 1]
    ok6 = (len(tb_marks) == xi["tbl_count"] and set(tb_marks) == exp_ids and not dup)
    rows.append(row("C6", "표 수", PASS if ok6 else FAIL,
                    {"XML표": xi["tbl_count"], "md표지": len(tb_marks), "중복또는누락": dup[:5]}))
    # C7 수식
    parser, pipe_cells = parse_md_tables(marked_md)
    eq_ids = ["EQ%0*d" % (doc.width["EQ"], i) for i in range(1, len(xi["eq_loc"]) + 1)]
    eq_marks = collections.Counter(re.findall(r"<!--(EQ\d+)-->", marked_md))
    bad_once = [e for e in eq_ids if eq_marks.get(e, 0) != 1]
    loc_bad = []
    for e, xl in zip(eq_ids, xi["eq_loc"]):
        ml = parser.eq_loc.get(e)
        if xl is None:
            if ml is not None:
                loc_bad.append((e, xl, ml))
        else:
            xl2 = ("TB%0*d" % (doc.width["TB"], xl[0]), xl[1], xl[2])
            if ml != xl2:
                loc_bad.append((e, xl2, ml))
    n_in_tbl = sum(1 for x in xi["eq_loc"] if x is not None)
    ok7 = not bad_once and not loc_bad and len(eq_ids) == len(doc.eqs)
    rows.append(row("C7", "수식 각각 1회", PASS if ok7 else FAIL,
                    {"XML수식": len(xi["eq_loc"]), "표안": n_in_tbl, "표밖": len(xi["eq_loc"]) - n_in_tbl,
                     "md표지": sum(eq_marks.values()), "1회아님": bad_once[:5], "칸어긋남": loc_bad[:5],
                     "칸어긋남수": len(loc_bad)}))
    # C8 그림·각주
    pic_xml = doc.counts.get("pic", 0)
    pic_md = len(re.findall(r"<!--(PIC\d+)-->", marked_md))
    files_missing = []
    not_conv = []
    for p in doc.pics:
        if p.src:
            from md_writer import image_rel
            rel = image_rel(p.src)
            if not os.path.exists(os.path.join(image_dir, os.path.basename(rel))):
                files_missing.append(rel)
            if os.path.splitext(rel)[1].lower() in (".wmf", ".emf", ".ole", ".hwp"):
                not_conv.append(rel)
    note_xml = doc.counts.get("footNote", 0) + doc.counts.get("endNote", 0)
    note_def = len(re.findall(r"(?m)^\[\^\d+\]: ", clean_md))
    note_ref = len(re.findall(r"\[\^\d+\](?!:)", clean_md))
    ok8 = pic_xml == pic_md and not files_missing and note_xml == note_def == note_ref
    v8 = PASS if ok8 and not not_conv else (WARN if ok8 else FAIL)
    rows.append(row("C8", "그림·각주·미주", v8,
                    {"XML그림": pic_xml, "md그림": pic_md, "그림파일없음": files_missing[:5], "미변환형식": not_conv[:5],
                     "XML각주미주": note_xml, "md각주정의": note_def, "md각주참조": note_ref}))
    # C9, C10 hwp2md 대조
    tag_rx = re.compile(r"</?[A-Za-z][^>]*>|!\[[^\]]*\]\([^)]*\)")
    big = letters_only(tag_rx.sub(" ", clean_md))
    if hwp2md_text is None:
        rows.append(row("C9", "우리 md 누락 후보", NA, {}, "hwp2md 변환 실패 또는 미실행"))
        rows.append(row("C10", "hwp2md가 뺀 내용", NA, {}))
        hw_empty = None
    else:
        txt = re.sub(r"\$\$.*?\$\$", "\n", hwp2md_text, flags=re.S)
        txt = re.sub(r"\$[^$\n]*\$", " ", txt)
        txt = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", txt)
        txt = re.sub(r"</?[A-Za-z][^>]*>", "\n", txt)
        units = []
        for ln in txt.split("\n"):
            for part in ln.split("|"):
                u = letters_only(part)
                if len(u) >= 8:
                    units.append(u)
        miss9 = [u for u in units if u not in big]
        # 수식 자리의 영문 글자가 hwp2md 쪽에만 남는 경우를 거르기 위해 한글만으로 한 번 더 비교한다
        hangul_only = lambda s: re.sub(r"[^가-힣]", "", s)  # noqa: E731
        no_eq = re.sub(r"(?<!\\)\$\$.*?\$\$|(?<!\\)\$[^$\n]*\$", " ", clean_md, flags=re.S)
        big_h = hangul_only(tag_rx.sub(" ", no_eq))
        miss9 = [u for u in miss9 if len(hangul_only(u)) >= 6 and hangul_only(u) not in big_h]
        rows.append(row("C9", "우리 md 누락 후보", PASS if not miss9 else WARN,
                        {"hwp2md문단수": len(units), "우리md에없음": len(miss9), "예시": miss9[:5]},
                        "정규화 차이(숫자 제거, 문장부호 제거)일 수 있음" if miss9 else ""))
        hw_big = letters_only(re.sub(r"\$\$.*?\$\$", "\n", hwp2md_text, flags=re.S))
        hw_set = set(units)
        lost = collections.Counter()
        lost_ex = {}
        for el, kind in t_contexts(doc):
            u = letters_only("".join(el.itertext()))
            if len(u) < 8:
                continue
            if u in hw_set or u in hw_big:
                continue
            lost[kind] += 1
            lost_ex.setdefault(kind, u[:30])
        rows.append(row("C10", "hwp2md가 뺀 내용", INFO, {"종류별": dict(lost), "예시": lost_ex},
                        "참고 지표(판정 없음)"))
        hp_parser, hp_pipe = parse_md_tables(hwp2md_text)
        hw_empty = count_empty_cells(hp_parser, hp_pipe)[0]
    # C11 한글 수식 문법 잔존, C13 LaTeX 형식
    # 글자 $ 는 md_writer가 &#36; 로 써 둔다. 풀면 수식 구분 기호와 섞이므로 \$ (글자 $)로 바꿔 둔다
    text_nohtml = html.unescape(clean_md.replace("&#36;", "\\$"))
    text_nocode = re.sub(r"(`+)\[수식 원문\].*?\1", "", text_nohtml, flags=re.S)
    text_nocode = re.sub(r"<code>.*?</code>", "", text_nocode, flags=re.S)
    spans = re.findall(r"(?<!\\)\$\$(.+?)\$\$", text_nocode, flags=re.S)
    rest = re.sub(r"(?<!\\)\$\$(.+?)\$\$", " ", text_nocode, flags=re.S)
    spans += re.findall(r"(?<!\\)\$(.+?)(?<!\\)\$", rest.replace("\\$", "\u0001"), flags=re.S)
    resid = []
    for sp in spans:
        t2 = re.sub(r"\\text\{[^}]*\}", "", sp)
        t2 = re.sub(r"\\[A-Za-z]+", "", t2)
        for wd in re.findall(r"[A-Za-z]{2,}", t2):
            if wd.lower() in DICTIONARY and wd not in SELF_VARS:
                resid.append(wd)
        if re.search(r"[`~#]", t2):
            resid.append("`~#")
    rows.append(row("C11", "한글 수식 문법 잔존", PASS if not resid else FAIL,
                    {"수식구간": len(spans), "잔존": len(resid), "예시": resid[:5]}))
    conv = [e for e in doc.eqs if e.res and e.res["status"] != "원문보존"]
    n_eq = len(doc.eqs)
    n_raw = n_eq - len(conv)
    if n_eq == 0:
        rows.append(row("C12", "수식 원문보존 비율", NA, {"수식": 0}))
    else:
        ratio = n_raw / n_eq
        rows.append(row("C12", "수식 원문보존 비율", PASS if ratio <= 0.05 else (WARN if ratio <= 0.20 else FAIL),
                        {"수식": n_eq, "원문보존": n_raw, "비율": round(ratio, 4),
                         "부분의심": sum(1 for e in conv if e.res["status"] == "부분의심")}))
    bad13 = [(e.id, p) for e in conv for p in check_latex(e.res["latex"])]
    rows.append(row("C13", "LaTeX 형식", PASS if not bad13 else FAIL, {"검사수식": len(conv), "문제": bad13[:5]}))
    # C14 빈 칸
    md_empty, md_total, per_tb = count_empty_cells(parser, pipe_cells)
    if md_empty == xi["empty_cells"]:
        v14 = PASS
    elif md_empty > xi["empty_cells"]:
        v14 = FAIL
    else:
        v14 = WARN
    rows.append(row("C14", "빈 표 칸", v14, {"XML빈칸": xi["empty_cells"], "md빈칸": md_empty,
                                          "XML전체칸": xi["total_cells"], "md전체칸": md_total,
                                          "hwp2md빈칸(참고)": hw_empty}))
    # C15 표 구조
    mismatch = [(i, xi["tbl_cells"][i], per_tb.get("TB%0*d" % (doc.width["TB"], i)))
                for i in xi["tbl_cells"] if per_tb.get("TB%0*d" % (doc.width["TB"], i)) != xi["tbl_cells"][i]]
    rows.append(row("C15", "표 구조", PASS if not xi["grid_problems"] and not mismatch else FAIL,
                    {"격자문제표": xi["grid_problems"][:5], "격자문제수": len(xi["grid_problems"]),
                     "md칸수불일치표": mismatch[:5], "불일치수": len(mismatch)}))
    # C16 미지원
    uc = collections.Counter(u.name for u in doc.unsup)
    rows.append(row("C16", "미지원 개체", PASS if not uc else WARN, dict(uc)))
    # C17 자동 번호
    an = doc.autonum
    cap_pat = r"<\s*(?:표|그림)\s*>"
    md_empty = len(re.findall(cap_pat, clean_md))
    # 문서에 자동 번호(autoNum)가 하나도 없으면 "<그림>"은 작성자가 번호 없이 쓴 원문 그대로이므로 변환기 잘못이 아니다.
    # (자동 번호가 있는 문서는 번호가 <표 와 > 사이에 따로 들어가 XML 글자만으로 구별할 수 없어서 예전처럼 엄격하게 본다)
    src_empty = md_empty if an["total"] == 0 else 0
    empty_caps = max(0, md_empty - src_empty)   # 변환기가 번호를 못 넣은 캡션
    ok17 = (an["total"] == an["rendered"] + an["suppressed"] + len(an["unknown"])
            and not an["unknown"] and empty_caps == 0)
    v17 = FAIL if (empty_caps or an["unknown"]) else (WARN if an["skips"] or an["nonDigit"] else PASS)
    note17 = "원문에 번호 없는 '<그림>'/'<표>' 캡션 %d개(원문 그대로)" % src_empty if src_empty else ""
    rows.append(row("C17", "자동 번호", v17, {"autoNum": an["total"], "숫자삽입": an["rendered"], "각주·쪽번호(미표시)": an["suppressed"],
                                         "건너뜀": an["skips"][:5], "건너뜀수": len(an["skips"]), "빈번호캡션": empty_caps,
                                         "원문빈번호캡션": src_empty,
                                         "숫자형식아님": an["nonDigit"], "알수없는종류": an["unknown"][:3]}, note17))
    # C18 목록 기호 이중
    dbl = [ln for ln in clean_md.split("\n") if re.match(r"^\s*- (\\?[%s])" % re.escape("".join(LIST_SYMBOLS)), ln)]
    rows.append(row("C18", "목록 기호 이중", PASS if not dbl else FAIL, {"개수": len(dbl), "예시": [d[:40] for d in dbl[:3]]}))
    # C19 제목
    hs = stats["headings"]
    skips = 0
    prev = 0
    for lv, _ in hs:
        if lv > prev + 1:
            skips += 1
        prev = lv
    longs = sum(1 for _, n in hs if n > 60)
    per_level = dict(collections.Counter(lv for lv, _ in hs))
    v19 = PASS
    notes = []
    if not hs or skips or longs:
        v19 = WARN
    if stats["heading_method"] == "글자패턴":
        v19 = WARN
        notes.append("제목판정=글자패턴")
    rows.append(row("C19", "제목", v19, {"제목수": len(hs), "단계별": per_level, "단계건너뜀": skips, "60자초과": longs,
                                       "판정방법": stats["heading_method"]}, "; ".join(notes)))
    # C20 원본 무변경
    h = extras.get("hash_before"), extras.get("hash_after")
    rows.append(row("C20", "원본 무변경", PASS if h[0] and h[0] == h[1] else FAIL,
                    {"검사파일": len(h[0] or {}), "변화": [k for k in (h[0] or {}) if (h[1] or {}).get(k) != h[0][k]]}))
    # C21 hwp 변환 충실도 (hwp 원본일 때만)
    rows.append(extras.get("c21") or row("C21", "hwp 변환 충실도", NA, {}, "원래 hwpx 문서"))
    return rows


def overall(rows):
    worst = max(WORST[r["verdict"]] for r in rows)
    return {0: PASS, 1: WARN, 2: FAIL}[worst]


def format_txt(key, rows):
    out = ["점검 보고서: %s" % key, "최종 판정: %s" % overall(rows), ""]
    for r in rows:
        out.append("%s %-16s [%s] %s" % (r["id"], r["name"], r["verdict"], r["note"]))
        out.append("    " + str(r["value"]))
    return "\n".join(out) + "\n"

