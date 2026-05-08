from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

# Add parent directory to path so we can import server module
sys.path.insert(0, str(Path(__file__).parent.parent))

import server


class ServerToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        self.old_root = server.PROJECT_ROOT
        server.PROJECT_ROOT = self.root

    def tearDown(self) -> None:
        server.PROJECT_ROOT = self.old_root
        self.temp_dir.cleanup()

    def test_create_and_read_file(self) -> None:
        create_result = server.create_file("sample.txt")
        self.assertTrue(create_result["success"])

        write_result = server.write_file(
            "sample.txt",
            "hello",
            mode="overwrite",
            confirm_overwrite=True,
        )
        self.assertTrue(write_result["success"])

        read_result = server.read_file("sample.txt")
        self.assertTrue(read_result["success"])
        self.assertEqual(read_result["data"]["content"], "hello")

    def test_write_requires_confirmation(self) -> None:
        (self.root / "safe.txt").write_text("old", encoding="utf-8")
        result = server.write_file("safe.txt", "new", mode="overwrite")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "invalid_input")

    def test_path_sandbox_blocks_escape(self) -> None:
        # Use forward slash which works on both Windows and Linux
        result = server.read_file("../outside.txt")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "permission_denied")

    def test_search_word_returns_matches(self) -> None:
        path = self.root / "notes.txt"
        path.write_text("Hello\nworld\nHELLO again", encoding="utf-8")

        result = server.search_word("notes.txt", "hello")
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["match_count"], 2)

    def test_analyze_python_file(self) -> None:
        path = self.root / "module.py"
        path.write_text("class A:\n    pass\n\ndef f():\n    return 1\n", encoding="utf-8")

        result = server.analyze_python_file("module.py")
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["classes"], 1)
        self.assertEqual(result["data"]["functions"], 1)


if __name__ == "__main__":
    unittest.main()
