import tomllib
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FiveWhRetirementContractTests(unittest.TestCase):
    def test_fivewh_route_is_retired_while_v118_asr_remains_available(self) -> None:
        from app.main import create_app

        app = create_app()
        paths = app.openapi()["paths"]

        self.assertIn("/v1.1.8/seacraft_asr", paths)
        self.assertIn("post", paths["/v1.1.8/seacraft_asr"])
        self.assertNotIn("/text/question", paths)
        self.assertEqual(TestClient(app).post("/text/question").status_code, 404)

    def test_routes_use_the_consolidated_asr_module(self) -> None:
        routes_dir = PROJECT_ROOT / "app/api/routes"

        self.assertTrue((routes_dir / "asr.py").is_file())
        self.assertFalse((routes_dir / "asr_v18.py").exists())
        self.assertFalse((routes_dir / "text.py").exists())

    def test_fivewh_runtime_identifiers_are_removed(self) -> None:
        source_expectations = {
            "app/core/models.py": (
                "BertForSequenceClassification",
                "BertTokenizer",
                "_model_bert",
                "_tokenizer",
                "_ensure_bert_loaded",
                "predict_fivewh",
            ),
            "app/core/config.py": (
                "bert_model_tokenizer",
                "bert_model_dir",
                "open_fivewh",
            ),
            "app/entity/data.py": (
                "class Segment(",
                "class SegmentRequestBody(",
            ),
            "app/utils/feature_utils.py": (
                "id2label",
                "def extract_features_segments(",
                "def merge_segments(",
                "def format_result(",
                "def reformat_result(",
            ),
        }

        for relative_path, retired_identifiers in source_expectations.items():
            source = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
            for identifier in retired_identifiers:
                with self.subTest(path=relative_path, identifier=identifier):
                    self.assertNotIn(identifier, source)

    def test_fivewh_config_and_models_are_excluded(self) -> None:
        with (PROJECT_ROOT / "config.toml").open("rb") as source:
            config = tomllib.load(source)

        self.assertNotIn("bert_model_tokenizer", config["model_paths"])
        self.assertNotIn("bert_model_dir", config["model_paths"])
        self.assertNotIn("open_fivewh", config["features"])

        dockerignore = set(
            (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        )
        self.assertIn("model/bert-base-chinese/", dockerignore)
        self.assertIn("model/bert_output/", dockerignore)


if __name__ == "__main__":
    unittest.main()
