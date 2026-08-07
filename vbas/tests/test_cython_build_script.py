import tempfile
import unittest
from pathlib import Path

from scripts.build_cython_modules import collect_extension_sources


class CythonBuildScriptTest(unittest.TestCase):
    def test_collect_extension_sources_keeps_init_and_explicit_entry_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package = root / "sample_pkg"
            package.mkdir()
            (package / "__init__.py").write_text("", encoding="utf-8")
            (package / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
            (package / "worker.py").write_text("VALUE = 2\n", encoding="utf-8")
            (package / "vendor").mkdir()
            (package / "vendor" / "third_party.py").write_text("VALUE = 3\n", encoding="utf-8")

            sources = collect_extension_sources(
                root=root,
                packages=["sample_pkg"],
                keep_sources={"sample_pkg/app.py"},
                exclude_globs=["sample_pkg/vendor/**"],
            )

        self.assertEqual([item.relative_path for item in sources], [Path("sample_pkg/worker.py")])
        self.assertEqual(sources[0].module_name, "sample_pkg.worker")

    def test_collect_extension_sources_skips_non_importable_python_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package = root / "sample_pkg"
            package.mkdir()
            (package / "__init__.py").write_text("", encoding="utf-8")
            (package / "worker.py").write_text("VALUE = 1\n", encoding="utf-8")
            (package / "worker-bak.py").write_text("VALUE = 2\n", encoding="utf-8")

            sources = collect_extension_sources(
                root=root,
                packages=["sample_pkg"],
            )

        self.assertEqual([item.relative_path for item in sources], [Path("sample_pkg/worker.py")])


if __name__ == "__main__":
    unittest.main()
