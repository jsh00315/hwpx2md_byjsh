"""수식 변환(한글 수식 -> LaTeX) 시험. 실행(저장소 폴더에서): python -X utf8 -m unittest discover -s tests -v
기대값은 실제 문서(2012~2025 전국·수도권 설명자료)에서 나온 수식과 PDF 대조로 확인한 규칙이다.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "converter"))
from hwpeq2latex import convert  # noqa: E402


class EquationTest(unittest.TestCase):
    def check(self, script, latex, status="변환"):
        r = convert(script)
        self.assertEqual(r["status"], status, script)
        if latex is not None:
            self.assertEqual(r["latex"], latex, script)

    def test_fraction(self):
        self.check("{V _{i}} over {C _{i}}", r"\frac{V_{i}}{C_{i}}")

    def test_times(self):
        self.check("a TIMES b", r"a\times b")

    def test_greek_with_subscripts(self):
        self.check("alpha _{B} + beta _{1}", r"\alpha_{B}+\beta_{1}")

    def test_sum_with_limits(self):
        self.check("sum _{m=1} ^{3} P _{i,j}", r"\sum_{m=1}^{3}P_{i,j}")

    def test_left_right(self):
        self.check("left ( a over b right )", r"\left( \frac{a}{b} \right)")

    def test_not_equal(self):
        self.check("x != y", r"x\neq y")

    def test_triangle_is_delta(self):
        # 사용자 지시 2026-11-12
        self.check("TRIANGLE x", r"\Delta x")

    def test_nu_is_letter_n(self):
        # PDF 대조(2012년판 NUZD, 2019년판 NumSta): NU 는 글자 N 하나로 그려진다. 사용자 지시 2026-11-13
        self.check("NU UZD", "NUZD")

    def test_unknown_syntax_is_kept_as_original(self):
        # 애매하면 변환하지 않고 원문을 보존한다 (중괄호 짝이 안 맞는 경우)
        self.check("zzzUNKNOWN_CMD{", None, status="원문보존")


if __name__ == "__main__":
    unittest.main()
