import tempfile
import unittest
from pathlib import Path

from evals.scripts.evalplus_prompt_hash import normalized_prompt_sha256
from evals.scripts import evalplus_runner as runner


class PromptHashTests(unittest.TestCase):
    def test_lf_crlf_cr_and_mixed_newlines_are_equivalent(self):
        text = "def solve():\n    # Unicode: café\n    pass\n"
        expected = runner.sha256_bytes(text.encode("utf-8"))
        variants = (text, text.replace("\n", "\r\n"), text.replace("\n", "\r"),
                    text.replace("\n", "\r\n", 1).replace("pass\n", "pass\r"))
        for variant in variants:
            for value in (variant, variant.encode("utf-8")):
                self.assertEqual(expected, normalized_prompt_sha256(value))

    def test_content_indentation_and_final_newline_mutations_are_rejected(self):
        original = "def solve():\n    return 1\n"
        expected = normalized_prompt_sha256(original)
        for changed in (original.replace("1", "2"), original.replace("    ", "  "), original.rstrip("\n"), " " + original):
            self.assertNotEqual(expected, normalized_prompt_sha256(changed.replace("\n", "\r\n")))

    def test_matches_pinned_runner_universal_newline_reading(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "solution.py"
            for value in (b"a\nb\n", b"a\r\nb\r\n", b"a\rb\r"):
                path.write_bytes(value)
                expected = runner.sha256_bytes(path.read_text(encoding="utf-8").encode("utf-8"))
                self.assertEqual(expected, normalized_prompt_sha256(path.read_bytes()))

    def test_byte_bound_hashes_are_not_normalized(self):
        self.assertNotEqual(runner.sha256_bytes(b"a\n"), runner.sha256_bytes(b"a\r\n"))

    def test_invalid_utf8_or_input_type_is_not_silently_repaired(self):
        with self.assertRaises(UnicodeDecodeError):
            normalized_prompt_sha256(b"\xff")
        with self.assertRaises(TypeError):
            normalized_prompt_sha256(None)


if __name__ == "__main__":
    unittest.main()
