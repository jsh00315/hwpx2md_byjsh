"""문서 하나를 변환하고 점검 보고서를 만든다 (hwpx 입력).
원본(hwp/hwpx/pdf)은 읽기만 하며, 실행 전후 SHA-256을 비교한다(C20).
"""
import csv
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import checker  # noqa: E402
from hwpeq2latex import convert_many  # noqa: E402
from hwpx_common import OUT_DIR, sha256_of  # noqa: E402
from hwpx_reader import HwpxDoc  # noqa: E402
from md_writer import Writer, image_rel, strip_sentinels  # noqa: E402

from config import HWP2MD, RHWP  # noqa: E402  (경로는 config.py 에서 고친다)
CORPUS_CSV = os.path.join(OUT_DIR, "_조사", "수식전수.csv")


def load_corpus_scripts():
    if not os.path.exists(CORPUS_CSV):
        return []
    with open(CORPUS_CSV, encoding="utf-8-sig", newline="") as f:
        return [r["원문"] for r in csv.DictReader(f)]


def export_images(doc, image_dir):
    """BinData를 images 폴더로 복사(원본 zip은 그대로). bmp는 png로 바꿔 본다."""
    os.makedirs(image_dir, exist_ok=True)
    warnings = []
    try:
        import pymupdf
    except Exception:
        pymupdf = None
    done = set()
    for p in doc.pics:
        if not p.src or p.src in done:
            continue
        done.add(p.src)
        data = doc.z.read(p.src)
        rel = os.path.basename(image_rel(p.src))
        ext_src = os.path.splitext(p.src)[1].lower()
        dest = os.path.join(image_dir, rel)
        if ext_src in (".bmp", ".tif", ".tiff"):
            try:
                pix = pymupdf.Pixmap(data)
                if pix.alpha or pix.colorspace.n > 3:
                    pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
                pix.save(dest)
                continue
            except Exception as e:  # 변환 실패하면 원형 그대로 둔다
                warnings.append("%s png 변환 실패(%s): 원형 유지" % (p.src, type(e).__name__))
                dest = os.path.join(image_dir, os.path.basename(p.src))
        with open(dest, "wb") as f:
            f.write(data)
    return warnings


_KIND = {}


def _hwp2md_kind():
    """HWP2MD 가 어느 프로그램인지 --help 로 알아낸다: 'to-md' 가 보이면 hephaex(cargo), 아니면 ilyos(pip)."""
    if HWP2MD not in _KIND:
        try:
            h = subprocess.run([HWP2MD, "--help"], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=60)
            _KIND[HWP2MD] = "hephaex" if "to-md" in (h.stdout + h.stderr) else "ilyos"
        except Exception:  # noqa
            _KIND[HWP2MD] = "hephaex"
    return _KIND[HWP2MD]


def run_hwp2md(path, out_md):
    if not HWP2MD:     # hwp2md.exe 가 없으면 대조 점검(C9, C10, C14)만 건너뛴다
        return None, "hwp2md.exe 없음(config.py 의 HWP2MD): 대조 점검 건너뜀"
    os.makedirs(os.path.dirname(out_md), exist_ok=True)
    try:
        if _hwp2md_kind() == "ilyos":
            # ilyos-numon/hwp2md (pip hwp2markdown): `hwp2md 입력 -o 출력폴더` -> 출력폴더/입력이름.md
            out_dir = os.path.dirname(out_md)
            cmd = [HWP2MD, path, "-o", out_dir]
            made = os.path.join(out_dir, os.path.splitext(os.path.basename(path))[0] + ".md")
        else:
            # hephaex/hwp2md (cargo): `hwp2md to-md 입력 -o 출력파일`
            cmd = [HWP2MD, "to-md", path, "-o", out_md]
            made = out_md
        # 파이프로 받으면 파이썬 프로그램이 cp949 로 '✓' 를 못 써서 죽으므로 utf-8 을 강제한다
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=900, env=env)
        if made != out_md and os.path.exists(made):
            os.replace(made, out_md)
    except Exception as e:  # noqa
        return None, "실행예외 " + repr(e)
    if r.returncode != 0 or not os.path.exists(out_md):
        return None, "exit=%s %s" % (r.returncode, r.stderr.strip()[:200])
    with open(out_md, encoding="utf-8", errors="replace") as f:
        return f.read(), ""


def pdf_stats(path):
    import pymupdf
    d = pymupdf.open(path)
    pages = d.page_count
    chars = sum(len(re.sub(r"\s+", "", pg.get_text())) for pg in d)
    d.close()
    return pages, chars


def check_c21(origin_hwp, hwpx_path, outdir, doc):
    """hwp 원본 대 rhwp 변환본: hwp2md 직접 변환의 표·그림 수, PDF 쪽 수와 글자 수 비교"""
    cdir = os.path.join(outdir, "c21_대조")
    os.makedirs(cdir, exist_ok=True)
    val = {}
    notes = []
    md, err = run_hwp2md(origin_hwp, os.path.join(cdir, "hwp직접_hwp2md.md"))
    if md is None:
        notes.append("hwp 직접 hwp2md 변환 실패: " + err)
    else:
        n_html = len(re.findall(r"<table", md))
        n_pipe = len(re.findall(r"(?m)^\|\s*-{3}", md))
        n_img = len(re.findall(r"!\[", md))
        val["hwp직접_표"] = n_html + n_pipe
        val["hwp직접_그림"] = n_img
        val["변환본_표"] = doc.counts.get("tbl", 0)
        val["변환본_그림"] = doc.counts.get("pic", 0)
        if (n_html + n_pipe) != doc.counts.get("tbl", 0) or n_img != doc.counts.get("pic", 0):
            ref_note = "참고: hwp2md의 hwp 직접 변환은 표/그림을 빼는 것으로 알려져 있어(계획 1장) 판정에서 제외"
            val["참고"] = ref_note
    pdfs = {}
    for label, src in (("원본hwp", origin_hwp), ("변환hwpx", hwpx_path)):
        dst = os.path.join(cdir, label + ".pdf")
        try:
            r = subprocess.run([RHWP, "export-pdf", src, "-o", dst], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=600)
            if r.returncode == 0 and os.path.exists(dst):
                pdfs[label] = pdf_stats(dst)
            else:
                notes.append("%s export-pdf 실패 exit=%s" % (label, r.returncode))
        except Exception as e:  # noqa
            notes.append("%s export-pdf 예외 %s" % (label, type(e).__name__))
    if len(pdfs) == 2:
        val["PDF쪽수(원본hwp,변환hwpx)"] = [pdfs["원본hwp"][0], pdfs["변환hwpx"][0]]
        val["PDF글자수(원본hwp,변환hwpx)"] = [pdfs["원본hwp"][1], pdfs["변환hwpx"][1]]
        if pdfs["원본hwp"] != pdfs["변환hwpx"]:
            notes.append("PDF 쪽/글자 수 차이")
    else:
        notes.append("PDF 비교 점검 불가")
    verdict = checker.PASS if not notes else checker.WARN
    return checker.row("C21", "hwp 변환 충실도", verdict, val, "; ".join(notes))


def convert_document(job, corpus):
    key = job["key"]
    hwpx = job["hwpx"]
    outdir = os.path.join(OUT_DIR, key)
    os.makedirs(outdir, exist_ok=True)
    guard = [hwpx] + ([job["origin_hwp"]] if job.get("origin_hwp") else []) + ([job["pdf"]] if job.get("pdf") else [])
    before = {p: sha256_of(p) for p in guard}

    doc = HwpxDoc(hwpx, key)
    results = convert_many([e.script for e in doc.eqs], corpus)
    for e, r in zip(doc.eqs, results):
        e.res = r
    ann = Writer(doc, markers=False, sentinel=True).render()
    wc = Writer(doc, markers=False, sentinel=False)
    clean = wc.render()
    marked = Writer(doc, markers=True, sentinel=False).render()
    same_clean = (strip_sentinels(ann) == clean)

    image_dir = os.path.join(outdir, "images")
    img_warn = export_images(doc, image_dir)
    hw_text, hw_err = run_hwp2md(hwpx, os.path.join(outdir, "hwp2md_대조", key + "_hwp2md.md"))

    after = {p: sha256_of(p) for p in guard}
    extras = {"hash_before": before, "hash_after": after}
    if job.get("origin_hwp"):
        if job.get("skip_c21"):  # run_all.py 설정으로 생략(느림): 생략했다고 분명히 적는다
            extras["c21"] = checker.row("C21", "hwp 변환 충실도", checker.NA, {}, "설정으로 생략(실행하지 않음)")
        else:
            extras["c21"] = check_c21(job["origin_hwp"], hwpx, outdir, doc)
    rows = checker.run_checks(doc, wc, ann, clean, marked, hw_text, image_dir, extras)
    final = checker.overall(rows)

    with open(os.path.join(outdir, key + ".md"), "w", encoding="utf-8-sig", newline="\n") as f:
        f.write(clean)
    with open(os.path.join(outdir, key + "_점검용.md"), "w", encoding="utf-8-sig", newline="\n") as f:
        f.write(marked)
    meta = {"문서키": key, "입력hwpx": hwpx, "최종판정": final,
            "개수": {"표": doc.counts.get("tbl", 0), "수식": len(doc.eqs), "그림": doc.counts.get("pic", 0),
                   "각주": len(doc.footnotes), "t": len(doc.t_all), "머리말꼬리말": len(doc.hf)},
            "clean과_표지제거본_동일": same_clean, "hwp2md_오류": hw_err, "그림경고": img_warn,
            "목록항목수": wc.stats["list_items"], "하이퍼링크등필드": dict(doc.fields)}
    with open(os.path.join(outdir, key + "_점검.json"), "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "checks": rows}, f, ensure_ascii=False, indent=1, default=str)
    with open(os.path.join(outdir, key + "_점검.txt"), "w", encoding="utf-8-sig") as f:
        f.write(checker.format_txt(key, rows))
    # 수식 대조 csv
    parser, pipe_cells = checker.parse_md_tables(marked)
    with open(os.path.join(outdir, key + "_수식대조.csv"), "w", encoding="utf-8-sig", newline="") as f:
        cw = csv.writer(f)
        cw.writerow(["id", "소속", "표시형태", "원문", "LaTeX", "상태", "사유"])
        for e in doc.eqs:
            loc = parser.eq_loc.get(e.id)
            where = "표 %s 행%d 열%d" % loc if loc else "본문"
            form = "독립" if ("<!--%s-->$$" % e.id) in marked else ("원문보존" if e.res["status"] == "원문보존" else "인라인")
            cw.writerow([e.id, where, form, e.script.replace("\n", "\\n"), e.res["latex"], e.res["status"],
                         "; ".join(e.res["reasons"])])
    with open(os.path.join(outdir, key + "_미지원목록.csv"), "w", encoding="utf-8-sig", newline="") as f:
        cw = csv.writer(f)
        cw.writerow(["id", "요소", "하위요소수", "글자"])
        for u in doc.unsup:
            cw.writerow([u.id, u.name, u.count, u.text[:80]])
    return {"key": key, "final": final, "rows": rows, "meta": meta, "outdir": outdir, "doc": doc,
            "clean": clean, "marked": marked}

