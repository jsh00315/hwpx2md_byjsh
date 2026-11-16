"""설정 파일: 경로는 여기 한 곳에서만 고친다.

내 컴퓨터에만 맞는 값은 이 파일을 고치지 말고 config_local.py 에 적는다 (git에 올라가지 않는다).
config_local.example.py 를 복사해서 config_local.py 로 만들면 된다.
"""
import os
import shutil

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))   # 저장소 맨 위 폴더


def _first_existing(*paths):
    for p in paths:
        if p and os.path.exists(p):
            return p
    return ""


# ----------------------------------------------------------------------------
# [1] 변환할 폴더와 결과 폴더
# ----------------------------------------------------------------------------
# 변환할 .hwp / .hwpx 가 들어 있는 폴더들 (하위 폴더까지 찾는다)
INPUT_DIRS = [os.path.join(ROOT_DIR, "input")]

# 결과를 쓸 폴더 (없으면 만든다. 원본이 있는 폴더와 다르게 한다)
OUT_ROOT = os.path.join(ROOT_DIR, "output")

# ----------------------------------------------------------------------------
# [2] 외부 프로그램 위치 (자동으로 찾고, 못 찾으면 빈 글자 "")
# ----------------------------------------------------------------------------
# rhwp.exe : .hwp 를 .hwpx 로 바꿀 때만 필요하다 (.hwpx 만 쓰면 없어도 된다)
#   받는 곳: https://github.com/edwardkim/rhwp/releases/latest
RHWP = _first_existing(
    os.environ.get("RHWP_PATH", ""),
    os.path.join(ROOT_DIR, "bin", "rhwp.exe"),
    shutil.which("rhwp"),
)

# hwp2md.exe : 점검(C9, C10, C14)에서 '두 번째 변환기'로 대조할 때만 쓴다. 없으면 그 점검만 건너뛴다.
#   설치: pip install hwp2markdown (ilyos, MIT, 권장) 또는 cargo install hwp2md (hephaex)
HWP2MD = _first_existing(
    os.environ.get("HWP2MD_PATH", ""),
    shutil.which("hwp2md"),
)

# ----------------------------------------------------------------------------
# [3] 고급
# ----------------------------------------------------------------------------
# run_all.py 말고 개별 스크립트를 쓸 때의 기본 결과 폴더
OUT_DIR = os.path.join(ROOT_DIR, "output", "_misc")

# 내 컴퓨터에만 맞는 값은 config_local.py 에서 덮어쓴다
try:
    from config_local import *  # noqa: F401,F403
except ImportError:
    pass
