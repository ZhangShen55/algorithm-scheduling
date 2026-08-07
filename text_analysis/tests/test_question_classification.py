import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.config import _load_from_config_file
from app.main import app
from app.models.entities import QuestionClassificationRequestObject, UsageInfo
from app.services.usecases.question_classification import (
    FIVE_WH_CATEGORIES,
    analyze_question_classification,
    reconstruct_question_candidates,
)


class QuestionClassificationConfigTests(unittest.TestCase):
    def test_loads_question_classification_concurrency_config(self):
        with TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.toml"
            cfg.write_text(
                """
api_key = "vllm"
base_url = "http://127.0.0.1:8855/v1"
model = "qwen3-8b"

[eval_weight]
base_score = 16.0

[question_classification]
llm_concurrency = 7
""".strip(),
                encoding="utf-8",
            )

            data = _load_from_config_file(str(cfg))

        self.assertEqual(data["QUESTION_CLASSIFICATION_LLM_CONCURRENCY"], 7)

    def test_defaults_question_classification_concurrency_to_four(self):
        with TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.toml"
            cfg.write_text(
                """
api_key = "vllm"
base_url = "http://127.0.0.1:8855/v1"
model = "qwen3-8b"

[eval_weight]
base_score = 16.0
""".strip(),
                encoding="utf-8",
            )

            data = _load_from_config_file(str(cfg))

        self.assertEqual(data["QUESTION_CLASSIFICATION_LLM_CONCURRENCY"], 4)


class QuestionClassificationPreparationTests(unittest.TestCase):
    def test_reconstructs_question_from_previous_segments_and_time_range(self):
        candidates = reconstruct_question_candidates(
            [
                {"segment_text": "今天课程开始。", "bg": 0, "ed": 2},
                {"segment_text": "接下来我们看这个案例，", "bg": 10, "ed": 12},
                {"segment_text": "它说明了", "bg": 12, "ed": 13},
                {"segment_text": "什么问题？", "bg": 13, "ed": 15},
            ],
            min_len=4,
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["text"], "接下来我们看这个案例，它说明了什么问题？")
        self.assertEqual(candidates[0]["time_range"], "10-15")

    def test_filters_short_reconstructed_questions(self):
        candidates = reconstruct_question_candidates(
            [{"segment_text": "谁？", "bg": 1, "ed": 2}],
            min_len=3,
        )

        self.assertEqual(candidates, [])


class QuestionClassificationUsecaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_analyze_aggregates_single_teacher_bucket_and_metadata(self):
        request = QuestionClassificationRequestObject(
            segments=[
                {"segment_text": "这个制度设计", "bg": 10, "ed": 12},
                {"segment_text": "为什么能够发挥作用？", "bg": 12, "ed": 15},
            ],
            min_len=3,
            confidence=0.8,
            task_id="task-1",
            course_id="course-1",
        )

        async def fake_classify(candidate):
            return {"is_valid": True, "category": "why"}, {
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
            }

        result, usage, used_model = await analyze_question_classification(
            request,
            classify_call=fake_classify,
        )

        self.assertEqual(used_model, "qwen3-8b")
        self.assertEqual(usage.total_tokens, 2)
        self.assertEqual(len(result), 1)
        bucket = result[0]
        self.assertEqual(bucket["role"], "teacher")
        self.assertEqual(bucket["task_id"], "task-1")
        self.assertEqual(bucket["course_id"], "course-1")
        self.assertEqual(bucket["confidence"], 0.8)
        self.assertEqual(bucket["min_len"], 3)
        self.assertEqual(bucket["why"]["count"], 1)
        self.assertEqual(
            bucket["why"]["question_info"],
            {"这个制度设计为什么能够发挥作用？": "10-15"},
        )
        for category in FIVE_WH_CATEGORIES:
            self.assertIn(category, bucket)

    async def test_analyze_drops_semantically_incomplete_question(self):
        request = QuestionClassificationRequestObject(
            segments=[{"segment_text": "对吧？", "bg": 1, "ed": 2}],
            min_len=1,
        )

        async def fake_classify(candidate):
            return {"is_valid": False, "category": "what"}, {
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
            }

        result, usage, _ = await analyze_question_classification(
            request,
            classify_call=fake_classify,
        )

        self.assertEqual(usage.total_tokens, 2)
        bucket = result[0]
        for category in FIVE_WH_CATEGORIES:
            self.assertEqual(bucket[category]["count"], 0)
            self.assertEqual(bucket[category]["question_info"], {})

    async def test_analyze_uses_configurable_llm_concurrency(self):
        request = QuestionClassificationRequestObject(
            segments=[
                {"segment_text": "第一问？", "bg": 1, "ed": 2},
                {"segment_text": "第二问？", "bg": 3, "ed": 4},
                {"segment_text": "第三问？", "bg": 5, "ed": 6},
                {"segment_text": "第四问？", "bg": 7, "ed": 8},
            ],
            min_len=1,
        )
        active = 0
        max_active = 0

        async def fake_classify(candidate):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.01)
            active -= 1
            return {"is_valid": True, "category": "what"}, {
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
            }

        result, usage, _ = await analyze_question_classification(
            request,
            classify_call=fake_classify,
            llm_concurrency=2,
        )

        self.assertEqual(max_active, 2)
        self.assertEqual(result[0]["what"]["count"], 4)
        self.assertEqual(usage.total_tokens, 8)


class QuestionClassificationApiTests(unittest.TestCase):
    def test_text_question_accepts_segments_without_role_and_compat_fields(self):
        async def fake_analyze(request):
            self.assertEqual(request.task_id, "task-api")
            self.assertEqual(request.course_id, "course-api")
            self.assertEqual(request.min_len, 3)
            self.assertEqual(len(request.segments), 2)
            return [
                {
                    "role": "teacher",
                    "task_id": request.task_id,
                    "course_id": request.course_id,
                    **{
                        category: {"count": 0, "question_info": {}}
                        for category in FIVE_WH_CATEGORIES
                    },
                }
            ], UsageInfo(total_tokens=0), "test-model"

        client = TestClient(app)
        with patch(
            "app.api.v1.routes.question_classification.analyze_question_classification",
            side_effect=fake_analyze,
        ):
            response = client.post(
                "/text/question",
                json={
                    "segments": [
                        {"segment_text": "请大家思考", "bg": 10, "ed": 11},
                        {"segment_text": "为什么会这样？", "bg": 11, "ed": 13},
                    ],
                    "min_len": 3,
                    "confidence": 0.5,
                    "task_id": "task-api",
                    "course_id": "course-api",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["model"], "test-model")
        self.assertEqual(len(payload["result"]), 1)
        self.assertEqual(payload["result"][0]["role"], "teacher")

    def test_text_question_rejects_empty_segments_with_400(self):
        client = TestClient(app)

        response = client.post("/text/question", json={"segments": []})

        self.assertEqual(response.status_code, 400)

    def test_sample_text_segments_build_role_free_requests(self):
        async def fake_analyze(request):
            self.assertGreater(len(request.segments), 0)
            for segment in request.segments:
                self.assertFalse(hasattr(segment, "role") and segment.role)
            return [
                {
                    "role": "teacher",
                    **{
                        category: {"count": 0, "question_info": {}}
                        for category in FIVE_WH_CATEGORIES
                    },
                }
            ], UsageInfo(), "test-model"

        client = TestClient(app)
        paths = sorted(
            path
            for path in Path("tests/text_segments").glob("*.json")
            if not path.name.endswith(".request.json")
        )
        self.assertGreater(len(paths), 0)

        with patch(
            "app.api.v1.routes.question_classification.analyze_question_classification",
            side_effect=fake_analyze,
        ):
            for path in paths:
                data = path.read_text(encoding="utf-8")
                payload = __import__("json").loads(data)
                source_segments = payload.get("textSegments", [])[:20]
                request_segments = [
                    {
                        "segment_text": item.get("segment_text") or item.get("text") or "",
                        "bg": item.get("bg"),
                        "ed": item.get("ed"),
                    }
                    for item in source_segments
                ]

                response = client.post("/text/question", json={"segments": request_segments})

                self.assertEqual(response.status_code, 200, path.name)
                result = response.json()["result"]
                self.assertEqual(len(result), 1, path.name)
                self.assertEqual(result[0]["role"], "teacher", path.name)
