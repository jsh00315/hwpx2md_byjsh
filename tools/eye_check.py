"""눈 대조 준비: 수식 표본을 골라 (왼쪽) 원본 hwp를 rhwp로 만든 PDF 쪽 그림과 (오른쪽) 변환 md를 KaTeX로 그린 화면을 나란히 보는 html 을 만든다.
주의: PDF 는 한글 프로그램이 아니라 rhwp 가 그린 것이다. 필요: pymupdf, rhwp.exe(config.py).
먼저 run_all.py 로 변환해 둔다. 점검요약_일괄.csv 에서 수식이 있는 문서 앞 3개를 고른다(N_DOCS).
사용(저장소 폴더에서): python -X utf8 -I tools/eye_check.py   결과: OUT_ROOT/눈대조/{문서키}/ (pdf, page_*.png, 대조.html, 표본.csv)
"""
import base64
import csv
import html
import json
import os
import re
import subprocess
import sys

import pymupdf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # config.py
from config import OUT_ROOT, RHWP  # noqa: E402

OUT = OUT_ROOT
N_SAMPLE = 6   # 문서마다 고르는 수식 개수
N_DOCS = 3     # 눈 대조할 문서 개수


def pick_docs():
    """점검요약_일괄.csv 에서 수식이 있는 문서 앞 N_DOCS 개: [(문서키, 원본경로)]"""
    rows = list(csv.DictReader(open(os.path.join(OUT, "점검요약_일괄.csv"), encoding="utf-8-sig")))
    rows = [r for r in rows if r.get("수식") not in (None, "", "0")]
    return [(r["문서키"], r["원본경로"]) for r in rows[:N_DOCS]]


def pick_samples(rows):
    raw = [r for r in rows if r["상태"] == "원문보존"]
    ok = [r for r in rows if r["상태"] != "원문보존"]
    tab = [r for r in ok if r["소속"] != "본문"]
    body = [r for r in ok if r["소속"] == "본문"]
    out = raw[:2]
    for grp, n in ((body, 2), (tab, N_SAMPLE)):
        step = max(1, len(grp) // (n + 1))
        out += (grp[step::step] or grp)[:n]
    seen, res = set(), []
    for r in out:
        if r["id"] not in seen and len(res) < N_SAMPLE:
            seen.add(r["id"])
            res.append(r)
    return res


def context_chunks(lines, i):
    txt = " ".join(re.sub(r"<[^>]+>|\$|\\|\*|&\w+;", " ", l) for l in lines[max(0, i - 8): i + 9])
    return [c for c in re.findall(r"[가-힣]{5,}", txt)]


def find_page(pdf, chunks):
    best, score = None, 0
    for pno, pg in enumerate(pdf):
        t = re.sub(r"\s+", "", pg.get_text())
        s = sum(1 for c in chunks if c in t)
        if s > score:
            best, score = pno, s
    return best, score


def main():
    for key, hwp in pick_docs():
        d = os.path.join(OUT, "눈대조", key)
        os.makedirs(d, exist_ok=True)
        pdfp = os.path.join(d, "원본_rhwp.pdf")
        if not os.path.exists(pdfp):
            subprocess.run([RHWP, "export-pdf", hwp, "-o", pdfp], capture_output=True, timeout=900)
        pdf = pymupdf.open(pdfp)
        rows = list(csv.DictReader(open(os.path.join(OUT, key, key + "_수식대조.csv"), encoding="utf-8-sig")))
        marked = open(os.path.join(OUT, key, key + "_점검용.md"), encoding="utf-8-sig").read().split("\n")
        items = []
        for r in pick_samples(rows):
            li = next((i for i, l in enumerate(marked) if "<!--%s-->" % r["id"] in l), None)
            pno, sc = find_page(pdf, context_chunks(marked, li)) if li is not None else (None, 0)
            png = ""
            if pno is not None:
                png = "page_%03d.png" % (pno + 1)
                pdf[pno].get_pixmap(matrix=pymupdf.Matrix(1.6, 1.6)).save(os.path.join(d, png))
            seg = "\n".join(marked[max(0, li - 6): li + 7]) if li is not None else ""
            seg = re.sub(r"<!--[A-Z]{2}\d+-->", "", seg)
            items.append({"id": r["id"], "소속": r["소속"], "상태": r["상태"], "원문": r["원문"], "LaTeX": r["LaTeX"],
                          "쪽": (pno + 1) if pno is not None else None, "일치단서": sc, "png": png, "md": seg,
                          "img": ("data:image/png;base64," + base64.b64encode(open(os.path.join(d, png), "rb").read()).decode()) if png else ""})
        with open(os.path.join(d, "표본.csv"), "w", encoding="utf-8-sig", newline="") as f:
            cw = csv.writer(f)
            cw.writerow(["id", "소속", "상태", "PDF쪽", "일치단서", "원문", "LaTeX"])
            for it in items:
                cw.writerow([it["id"], it["소속"], it["상태"], it["쪽"], it["일치단서"], it["원문"], it["LaTeX"]])
        page = HTML.replace("__DATA__", json.dumps(items, ensure_ascii=False).replace("</", "<\\/")).replace("__KEY__", html.escape(key))
        with open(os.path.join(d, "대조.html"), "w", encoding="utf-8") as f:
            f.write(page)
        print(key, [(it["id"], it["쪽"], it["일치단서"]) for it in items])


HTML = """<!doctype html><meta charset="utf-8"><title>__KEY__</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.9/katex.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/markdown-it/13.0.2/markdown-it.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.9/katex.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/markdown-it-texmath@1.0.0/texmath.min.js"></script>
<style>body{font:14px sans-serif;margin:8px}.row{display:flex;gap:10px;border-bottom:3px solid #888;margin-bottom:14px}
.row>div{flex:1;min-width:0}img{width:100%}table{border-collapse:collapse}td,th{border:1px solid #999;padding:2px}
h4{margin:4px 0;background:#eee}</style>
<body><div id="root"></div><script>
const items=__DATA__;
const md=window.markdownit({html:true}).use(window.texmath,{engine:window.katex,delimiters:'dollars'});
const root=document.getElementById('root');
for(const it of items){const row=document.createElement('div');row.className='row';
row.innerHTML='<div><h4>'+it.id+' ('+it.소속+', 상태 '+it.상태+', PDF '+it.쪽+'쪽)</h4>'+(it.img?'<img src="'+it.img+'">':'(PDF 쪽 못 찾음)')+'</div><div><h4>변환 md 화면</h4>'+md.render(it.md)+'<hr><small>LaTeX: <code></code></small></div>';
row.querySelector('code').textContent=it.LaTeX||'(원문보존)';root.appendChild(row);}
</script>"""

if __name__ == "__main__":
    main()
