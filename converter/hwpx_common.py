"""hwpx 공통 도우미: zip 열기, spine 순서 읽기, 태그 이름 분해, 분류표 읽기 (파일 읽기 전용)."""
import csv
import hashlib
import os
import re
import sys
import zipfile
from xml.etree import ElementTree as ET

TOOL_DIR = os.path.dirname(os.path.abspath(__file__))     # converter 폴더
ROOT_DIR = os.path.dirname(TOOL_DIR)                       # 저장소 맨 위 폴더
RULES_DIR = os.path.join(ROOT_DIR, "rules")                # 규칙 csv 폴더
sys.path.insert(0, TOOL_DIR)
sys.path.insert(0, ROOT_DIR)   # config.py 를 찾게 한다
from config import OUT_DIR  # noqa: E402,F401  (경로는 config.py 에서 고친다)
CLASS_CSV = os.path.join(RULES_DIR, "요소분류표.csv")

HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
HC = "http://www.hancom.co.kr/hwpml/2011/core"
HH = "http://www.hancom.co.kr/hwpml/2011/head"
OPF = "http://www.idpf.org/2007/opf/"

# 흔한 접두어 (보고서 표시용; 실제 판정은 URI로 한다)
PREFIX = {
    HP: "hp", HC: "hc", HH: "hh", OPF: "opf",
    "http://www.hancom.co.kr/hwpml/2011/section": "hs",
    "http://www.hancom.co.kr/hwpml/2016/paragraph": "hp10",
    "http://www.hancom.co.kr/hwpml/2011/master-page": "hm",
    "": "",
}


def split_tag(tag):
    """'{uri}name' -> (uri, name)"""
    if tag.startswith("{"):
        uri, name = tag[1:].split("}", 1)
        return uri, name
    return "", tag


def sha256_of(path):
    """파일 SHA-256 (읽기만)"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def head_bytes(path, n=4):
    with open(path, "rb") as f:
        return f.read(n)


def read_spine(z):
    """content.hpf의 spine 순서대로 section 파트 이름을 돌려준다. 없으면 번호 순 정렬."""
    names = z.namelist()
    sections = []
    manifest = {}
    if "Contents/content.hpf" in names:
        root = ET.fromstring(z.read("Contents/content.hpf"))
        for it in root.iter("{%s}item" % OPF):
            manifest[it.get("id")] = (it.get("href"), it.get("media-type"))
        for ref in root.iter("{%s}itemref" % OPF):
            href = manifest.get(ref.get("idref"), (None,))[0]
            if href and re.search(r"section\d+\.xml$", href):
                sections.append(href)
    if not sections:
        sections = sorted(
            [n for n in names if re.match(r"Contents/section\d+\.xml$", n)],
            key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)))
    return sections, manifest


def load_classification():
    """요소분류표.csv -> {(uri, name): (분류, 처리방법)}"""
    table = {}
    if not os.path.exists(CLASS_CSV):
        return table
    with open(CLASS_CSV, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            table[(row["ns_uri"], row["요소"])] = (row["분류"], row["처리방법"])
    return table
