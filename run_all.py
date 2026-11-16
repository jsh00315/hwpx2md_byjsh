"""폴더 안의 .hwp / .hwpx 를 한 번에 md 로 변환하고, 문서마다 점검 한 줄 요약 CSV 를 만든다.

사용: config.py(또는 config_local.py)의 INPUT_DIRS(변환할 폴더)와 OUT_ROOT(결과 폴더)를 고친 뒤, 저장소 폴더에서
    python -X utf8 -I run_all.py
(폴더 경로는 명령줄 인자가 아니라 설정 파일에 적는다. 한글 경로를 인자로 넘기면 깨지는 문제를 피하려는 것이다.)

하는 일
  1. INPUT_DIRS 아래 .hwp/.hwpx 를 모두 찾는다 (OUT_ROOT, 이름이 EXCLUDE_DIR_PREFIX 로 시작하는 폴더는 제외).
  2. SHA-256 이 같은 파일은 한 번만 변환한다 (나머지는 중복목록.csv 에 기록).
  3. .hwp 는 rhwp export-hwpx 로 hwpx 로 바꾼 뒤, hwpx 와 똑같은 변환기(convert_doc.convert_document)에 넣는다.
  4. 문서마다 자동 점검 C1~C21 을 돌리고 점검요약_일괄.csv 에 한 줄씩 쓴다.
  5. 규칙표에 없어서 원문으로 남긴 수식 명령은 미지원수식명령_일괄.csv 에 모은다.
원본(hwp/hwpx)은 읽기만 한다. 결과는 모두 OUT_ROOT 아래에만 쓴다.
"""
import collections
import csv
import os
import re
import subprocess
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)                                   # config.py
sys.path.insert(0, os.path.join(HERE, "converter"))        # 변환기 파일들
# 경로 설정은 config.py 한 곳에서 한다 (변환할 폴더 INPUT_DIRS, 결과 폴더 OUT_ROOT, rhwp.exe, hwp2md.exe)
from config import HWP2MD, INPUT_DIRS, OUT_ROOT, RHWP  # noqa: E402,F401
import convert_doc  # noqa: E402
from hwpx_common import head_bytes, sha256_of  # noqa: E402

EXCLUDE_DIR_PREFIX = ("_", ".")  # 이 글자로 시작하는 폴더는 건너뜀
HWP_C21 = False   # True 면 hwp 문서마다 C21(PDF 두 번 만들기 + hwp2md)까지 돌린다. 느리다.


class Tee:
    def __init__(self, *outs):
        self.outs = outs

    def write(self, s):
        for o in self.outs:
            o.write(s)

    def flush(self):
        for o in self.outs:
            o.flush()


def find_files():
    """대상 파일 목록 (경로 정렬 -> 같은 해시 중 먼저 나오는 것이 대표)"""
    found = []
    skip_root = os.path.normcase(os.path.abspath(OUT_ROOT))
    for base in INPUT_DIRS:
        for dp, dns, fns in os.walk(base):
            dns[:] = sorted(d for d in dns if not d.startswith(EXCLUDE_DIR_PREFIX)
                            and os.path.normcase(os.path.abspath(os.path.join(dp, d))) != skip_root)
            for fn in sorted(fns):
                if os.path.splitext(fn)[1].lower() in (".hwp", ".hwpx") and not fn.startswith("~$"):
                    found.append(os.path.join(dp, fn))
    seen, out = set(), []
    for p in found:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def make_key(path, used, digest):
    stem = os.path.splitext(os.path.basename(path))[0]
    folder = os.path.basename(os.path.dirname(path))     # 문서키 = 폴더이름__파일이름
    key = folder + "__" + stem
    key = re.sub(r'[\\/:*?"<>|\s]', "_", key)
    if key in used:                      # 같은 키가 겹치면 해시 앞 8자리를 붙인다
        key = key + "_" + digest[:8]
    used.add(key)
    return key


def hwp_to_hwpx(src, dst):
    """rhwp export-hwpx. 성공 판정: 종료 코드 0 + 출력 파일 + 앞 2바이트 PK"""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if not RHWP:
        return False, "rhwp.exe 위치를 못 찾음: config.py 의 RHWP 를 고치세요 (.hwp 를 바꿀 때만 필요)"
    try:
        r = subprocess.run([RHWP, "export-hwpx", src, dst], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=600)
    except Exception as e:  # noqa
        return False, "실행예외 " + repr(e)
    ok = r.returncode == 0 and os.path.exists(dst) and head_bytes(dst, 2) == b"PK"
    return ok, "exit=%s %s" % (r.returncode, (r.stdout + r.stderr).strip()[:200])


def main():
    os.makedirs(OUT_ROOT, exist_ok=True)
    sys.stdout = Tee(sys.stdout, open(os.path.join(OUT_ROOT, "실행로그_일괄.txt"), "w", encoding="utf-8-sig"))
    convert_doc.OUT_DIR = OUT_ROOT       # convert_document 가 결과를 OUT_ROOT 아래에 쓰게 한다
    corpus = convert_doc.load_corpus_scripts()

    files = find_files()
    print("찾은 파일: %d개 (폴더: %s)" % (len(files), "; ".join(INPUT_DIRS)))
    reps, dups, by_hash, used = [], [], {}, set()
    for p in files:
        h = sha256_of(p)
        if h in by_hash:
            dups.append((p, by_hash[h]["path"], h))
        else:
            rep = {"path": p, "sha": h, "key": make_key(p, used, h), "dup_n": 0}
            by_hash[h] = rep
            reps.append(rep)
    for _, rp, h in dups:
        by_hash[h]["dup_n"] += 1
    print("대표(변환 대상) %d개 / 같은 내용 중복 %d개" % (len(reps), len(dups)))
    with open(os.path.join(OUT_ROOT, "중복목록.csv"), "w", encoding="utf-8-sig", newline="") as f:
        cw = csv.writer(f)
        cw.writerow(["중복파일", "대표파일", "sha256"])
        cw.writerows(dups)

    summary, unsup = [], collections.OrderedDict()
    for rep in reps:
        path, key = rep["path"], rep["key"]
        ext = os.path.splitext(path)[1].lower()
        row = {"문서키": key, "원본경로": path, "형식": ext[1:], "중복개수": rep["dup_n"]}
        print("== 변환 시작:", key, flush=True)
        job = {"key": key}
        try:
            if ext == ".hwp":
                if head_bytes(path, 2) == b"PK":      # 확장자만 hwp 이고 실제는 hwpx(zip)
                    job["hwpx"] = path
                else:
                    dst = os.path.join(OUT_ROOT, "_hwpx변환본", key + ".hwpx")
                    ok, msg = hwp_to_hwpx(path, dst)
                    print("   rhwp 변환:", "성공" if ok else "실패", msg, flush=True)
                    if not ok:
                        row.update({"최종판정": "변환실패", "실패사유": "rhwp export-hwpx 실패: " + msg})
                        summary.append(row)
                        continue
                    job["hwpx"] = dst
                    job["origin_hwp"] = path
                    job["skip_c21"] = not HWP_C21
            else:
                job["hwpx"] = path
            res = convert_doc.convert_document(job, corpus)
            row["최종판정"] = res["final"]
            for r in res["rows"]:
                row[r["id"]] = r["verdict"]
            cnt = res["meta"]["개수"]
            row.update({"표": cnt["표"], "수식": cnt["수식"], "그림": cnt["그림"], "각주": cnt["각주"]})
            c12 = next(r["value"] for r in res["rows"] if r["id"] == "C12")
            row["원문보존수식"] = c12.get("원문보존")
            row["원문보존비율"] = c12.get("비율")
            row["실패사유"] = "; ".join("%s %s" % (r["id"], r["note"] or r["name"]) for r in res["rows"] if r["verdict"] == "실패")
            row["경고사유"] = "; ".join("%s %s" % (r["id"], r["note"] or r["name"]) for r in res["rows"] if r["verdict"] == "경고")
            row["요약"] = "표%d 수식%d 그림%d 각주%d | 원문보존수식 %s | 판정 %s | 실패 %d 경고 %d" % (
                cnt["표"], cnt["수식"], cnt["그림"], cnt["각주"], c12.get("원문보존"), res["final"],
                sum(r["verdict"] == "실패" for r in res["rows"]), sum(r["verdict"] == "경고" for r in res["rows"]))
            print("   " + row["요약"], flush=True)
            # 규칙표에 없는 수식 명령 모으기 (원문 보존된 수식만)
            for e in res["doc"].eqs:
                if e.res["status"] != "원문보존":
                    continue
                items = [(w, "분류%d" % c) for w, c in e.res["unsupported"]] or \
                        [("", "구문/형식: " + "; ".join(e.res["reasons"])[:80])]
                for w, kind in items:
                    rec = unsup.setdefault((w, kind), {"개수": 0, "docs": set(), "예시키": key, "예시id": e.id,
                                                        "예시원문": e.script.replace("\n", "\\n")[:120]})
                    rec["개수"] += 1
                    rec["docs"].add(key)
        except Exception:
            traceback.print_exc()
            row.update({"최종판정": "실행오류", "실패사유": traceback.format_exc()[-300:]})
        summary.append(row)

    cols = ["문서키", "원본경로", "형식", "중복개수", "최종판정", "표", "수식", "그림", "각주", "원문보존수식", "원문보존비율"] \
        + ["C%d" % i for i in range(1, 22)] + ["요약", "실패사유", "경고사유"]
    with open(os.path.join(OUT_ROOT, "점검요약_일괄.csv"), "w", encoding="utf-8-sig", newline="") as f:
        cw = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        cw.writeheader()
        cw.writerows(summary)
    with open(os.path.join(OUT_ROOT, "미지원수식명령_일괄.csv"), "w", encoding="utf-8-sig", newline="") as f:
        cw = csv.writer(f)
        cw.writerow(["단어", "종류", "개수", "문서수", "예시문서키", "예시수식id", "예시원문"])
        for (w, kind), v in sorted(unsup.items(), key=lambda kv: -kv[1]["개수"]):
            cw.writerow([w, kind, v["개수"], len(v["docs"]), v["예시키"], v["예시id"], v["예시원문"]])
    n = collections.Counter(s["최종판정"] for s in summary)
    print("판정 집계:", dict(n))
    print("미지원 수식 명령 종류: %d개 -> 미지원수식명령_일괄.csv" % len(unsup))
    print("결과 폴더:", OUT_ROOT)
    bad = sum(v for k, v in n.items() if k != "합격")
    sys.exit(0 if bad == 0 else 1)


if __name__ == "__main__":
    main()
