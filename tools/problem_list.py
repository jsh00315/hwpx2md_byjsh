"""변환 결과(OUT_ROOT)에서 문제인 부분(원문 그대로 남은 수식, 미지원 개체, 번호 건너뜀, 제목 판정 등)을 문서별로 모아 문제부분목록.md 로 쓴다.
먼저 run_all.py 로 변환해 둔다. 사용(저장소 폴더에서): python -X utf8 -I tools/problem_list.py  (읽기 전용, 결과는 OUT_ROOT 안에만 쓴다)
"""
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # config.py
from config import OUT_ROOT  # noqa: E402

B = OUT_ROOT


def read_csv(p):
    return list(csv.DictReader(open(p, encoding="utf-8-sig")))


out = ["# 변환 결과에서 문제인 부분 목록\n",
       "md 줄 번호는 `{문서키}\\{문서키}.md` 기준이다.\n"]
tot_raw = 0
for r in read_csv(os.path.join(B, "점검요약_일괄.csv")):
    k = r["문서키"]
    d = os.path.join(B, k)
    md = open(os.path.join(d, k + ".md"), encoding="utf-8-sig").read().split("\n")
    sec = []
    raw = [x for x in read_csv(os.path.join(d, k + "_수식대조.csv")) if x["상태"] == "원문보존"]
    lines = [i + 1 for i, l in enumerate(md) if "[수식 원문]" in l]
    if raw:
        tot_raw += len(raw)
        sec.append("- 수식이 원문 그대로 남음 %d개 (md 줄: %s%s)" % (
            len(raw), ", ".join(map(str, lines[:25])), " …" if len(lines) > 25 else ""))
        for x in raw[:6]:
            sec.append("    - %s (%s) 이유: %s / 원문: `%s`" % (x["id"], x["소속"], x["사유"][:60], x["원문"][:70]))
    un = read_csv(os.path.join(d, k + "_미지원목록.csv"))
    if un:
        ul = [i + 1 for i, l in enumerate(md) if "[미지원" in l]
        sec.append("- 미지원 개체 %d개 (%s) (md 줄: %s)" % (
            len(un), ",".join(sorted({u["요소"] for u in un})), ", ".join(map(str, ul[:10]))))
    j = json.load(open(os.path.join(d, k + "_점검.json"), encoding="utf-8"))
    for c in j["checks"]:
        if c["id"] == "C17" and c["value"].get("건너뜀수"):
            sec.append("- 표·그림 번호 건너뜀 %d곳: %s" % (c["value"]["건너뜀수"], c["value"]["건너뜀"]))
        if c["id"] == "C9" and c["verdict"] == "경고":
            sec.append("- 보조 변환기(hwp2md)에는 있는데 우리 md에 없는 문단 있음: %s" % str(c["value"])[:200])
        if c["id"] == "C19":
            sec.append("- 제목(#) 인식: %s개, 판정방법 %s" % (c["value"].get("제목수"), c["value"].get("판정방법")))
    out.append("\n## %s\n" % k)
    out += sec or ["- (문제 없음)"]
out.append("\n## 합계\n- 수식 원문 보존: 전체 %d개\n" % tot_raw)
open(os.path.join(B, "문제부분목록.md"), "w", encoding="utf-8-sig").write("\n".join(out))
print("\n".join(out))
