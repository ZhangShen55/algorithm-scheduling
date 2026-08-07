import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from app.core.config import _load_from_config_file
from app.models.entities import CourseKnowledgeCorpusAnalysisRequestObject, TextSegment
from app.services.usecases.course_knowledge_corpus_analysis import (
    analyze_course_knowledge_corpus,
    clean_candidate_payload,
    dedupe_candidates,
    filter_effective_segments,
    sanitize_final_result,
    split_segments_into_chunks,
)


class CourseKnowledgeCorpusConfigTests(unittest.TestCase):
    def test_loads_course_knowledge_corpus_config(self):
        with TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.toml"
            cfg.write_text(
                """
api_key = "vllm"
base_url = "http://127.0.0.1:8855/v1"
model = "qwen3-8b"

[eval_weight]
base_score = 16.0

[course_knowledge_corpus_analysis]
chunk_chars = 500
chunk_overlap_chars = 60
max_chunks = 7
chunk_concurrency = 3
chunk_retry_attempts = 4
final_retry_attempts = 5
chunk_max_knowledge_points = 2
chunk_max_corpus = 3
final_max_knowledge_points = 9
final_max_corpus = 10
max_description_chars = 88
max_corpus_content_chars = 180
""".strip(),
                encoding="utf-8",
            )

            data = _load_from_config_file(str(cfg))

        self.assertEqual(data["COURSE_KNOWLEDGE_CORPUS_ANALYSIS_CHUNK_CHARS"], 500)
        self.assertEqual(data["COURSE_KNOWLEDGE_CORPUS_ANALYSIS_CHUNK_OVERLAP_CHARS"], 60)
        self.assertEqual(data["COURSE_KNOWLEDGE_CORPUS_ANALYSIS_MAX_CHUNKS"], 7)
        self.assertEqual(data["COURSE_KNOWLEDGE_CORPUS_ANALYSIS_CHUNK_CONCURRENCY"], 3)
        self.assertEqual(data["COURSE_KNOWLEDGE_CORPUS_ANALYSIS_CHUNK_RETRY_ATTEMPTS"], 4)
        self.assertEqual(data["COURSE_KNOWLEDGE_CORPUS_ANALYSIS_FINAL_RETRY_ATTEMPTS"], 5)
        self.assertEqual(data["COURSE_KNOWLEDGE_CORPUS_ANALYSIS_CHUNK_MAX_KNOWLEDGE_POINTS"], 2)
        self.assertEqual(data["COURSE_KNOWLEDGE_CORPUS_ANALYSIS_CHUNK_MAX_CORPUS"], 3)
        self.assertEqual(data["COURSE_KNOWLEDGE_CORPUS_ANALYSIS_FINAL_MAX_KNOWLEDGE_POINTS"], 9)
        self.assertEqual(data["COURSE_KNOWLEDGE_CORPUS_ANALYSIS_FINAL_MAX_CORPUS"], 10)
        self.assertEqual(data["COURSE_KNOWLEDGE_CORPUS_ANALYSIS_MAX_DESCRIPTION_CHARS"], 88)
        self.assertEqual(data["COURSE_KNOWLEDGE_CORPUS_ANALYSIS_MAX_CORPUS_CONTENT_CHARS"], 180)


class CourseKnowledgeCorpusPreparationTests(unittest.TestCase):
    def test_filters_course_range_and_breaks(self):
        segments = [
            {"text": "课前闲聊。", "bg": 0, "ed": 9},
            {"text": "正式讲人工智能。", "bg": 10, "ed": 20},
            {"text": "课间聊天。", "bg": 21, "ed": 30},
            {"text": "继续讲履职能力。", "bg": 31, "ed": 40},
            {"text": "课后闲聊。", "bg": 41, "ed": 50},
        ]

        filtered, meta = filter_effective_segments(
            segments,
            course_start=10,
            course_end=40,
            breaks=[{"start": 20, "end": 31}],
        )

        self.assertEqual([seg.text for seg in filtered], ["正式讲人工智能。", "继续讲履职能力。"])
        self.assertEqual(meta["input_segments"], 5)
        self.assertEqual(meta["used_segments"], 2)
        self.assertEqual(meta["removed_break_segments"], 1)

    def test_splits_long_effective_segments_near_500_chars_with_overlap(self):
        segments = [
            {"text": "甲" * 250, "bg": 10, "ed": 20},
            {"text": "乙" * 250, "bg": 20, "ed": 30},
            {"text": "丙" * 250, "bg": 30, "ed": 40},
        ]

        chunks = split_segments_into_chunks(
            segments,
            chunk_chars=500,
            overlap_chars=60,
            max_chunks=4,
        )

        self.assertEqual(len(chunks), 2)
        self.assertEqual(len(chunks[0]["text"]), 500)
        self.assertTrue(chunks[1]["text"].startswith(chunks[0]["text"][-60:]))
        self.assertLessEqual(chunks[1]["char_count"], 560)

    def test_cleans_normalizes_limits_and_dedupes_chunk_payload(self):
        cleaned = clean_candidate_payload(
            {
                "knowledge_points": [
                    {"title": "人工智能工具", "description": "说明 AI 工具在履职中的辅助作用。", "score": 99},
                    {"title": "人工智能工具", "description": "重复标题应去掉。"},
                    {"title": "然后", "description": "泛化口头词应去掉。"},
                    {"title": "", "description": "空标题应去掉。"},
                    "not-a-dict",
                ],
                "corpus": [
                    {
                        "content": "人工智能工具可以辅助材料分析和内容生成。",
                        "description": "说明 AI 工具的履职辅助作用。",
                        "time_range": {"start": 1, "end": 2},
                    },
                    {"content": "人工智能工具可以辅助材料分析和内容生成。", "description": "重复内容应去掉。"},
                    {"content": "嗯嗯", "description": "过短语料应去掉。"},
                ],
            },
            max_knowledge_points=3,
            max_corpus=3,
            max_description_chars=80,
            max_corpus_content_chars=120,
        )

        self.assertEqual(
            cleaned,
            {
                "knowledge_points": [{"title": "人工智能工具", "description": "说明AI工具在履职中的辅助作用。"}],
                "corpus": [
                    {
                        "content": "人工智能工具可以辅助材料分析和内容生成。",
                        "description": "说明AI工具的履职辅助作用。",
                    }
                ],
            },
        )
        for item in cleaned["knowledge_points"]:
            self.assertEqual(set(item.keys()), {"title", "description"})
        for item in cleaned["corpus"]:
            self.assertEqual(set(item.keys()), {"content", "description"})

    def test_dedupes_duplicate_titles_and_duplicate_corpus_content(self):
        data = dedupe_candidates(
            {
                "knowledge_points": [
                    {"title": "人工智能工具", "description": "第一条。"},
                    {"title": " 人工 智能 工具 ", "description": "重复标题。"},
                    {"title": "履职能力", "description": "第二条。"},
                ],
                "corpus": [
                    {"content": "人工智能工具可以辅助材料分析。", "description": "第一条。"},
                    {"content": "人工 智能 工具 可以 辅助 材料 分析。", "description": "重复内容。"},
                    {"content": "履职能力建设需要结合岗位职责。", "description": "第二条。"},
                ],
            }
        )

        self.assertEqual([item["title"] for item in data["knowledge_points"]], ["人工智能工具", "履职能力"])
        self.assertEqual(
            [item["content"] for item in data["corpus"]],
            ["人工智能工具可以辅助材料分析。", "履职能力建设需要结合岗位职责。"],
        )

    def test_sanitizes_final_result_shape_and_removes_diagnostics(self):
        result = sanitize_final_result(
            {
                "knowledge_points": [
                    {
                        "title": "人工智能工具辅助履职",
                        "description": "说明 AI 工具在履职场景中的应用。",
                        "confidence": 0.9,
                    },
                    {"title": "人工智能工具辅助履职", "description": "重复标题应去掉。"},
                    {"title": "履职能力", "description": "指完成岗位职责和推动工作的综合能力。"},
                ],
                "corpus": [
                    {
                        "content": "人工智能工具可以辅助完成材料分析、内容生成和工作总结。",
                        "description": "说明 AI 工具对履职效率的提升。",
                        "evidence": "不应返回",
                    },
                    {
                        "content": "人工智能工具可以辅助完成材料分析、内容生成和工作总结。",
                        "description": "重复内容应去掉。",
                    },
                ],
                "hotwords": [{"word": "人工智能"}],
            },
            max_knowledge_points=10,
            max_corpus=10,
            max_description_chars=80,
            max_corpus_content_chars=120,
        )

        self.assertEqual(
            result,
            {
                "knowledge_points": [
                    {"title": "人工智能工具辅助履职", "description": "说明AI工具在履职场景中的应用。"},
                    {"title": "履职能力", "description": "指完成岗位职责和推动工作的综合能力。"},
                ],
                "corpus": [
                    {
                        "content": "人工智能工具可以辅助完成材料分析、内容生成和工作总结。",
                        "description": "说明AI工具对履职效率的提升。",
                    }
                ],
            },
        )
        self.assertNotIn("hotwords", result)
        for item in result["knowledge_points"]:
            self.assertEqual(set(item.keys()), {"title", "description"})
        for item in result["corpus"]:
            self.assertEqual(set(item.keys()), {"content", "description"})


class CourseKnowledgeCorpusUsecaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_analyze_returns_simplified_knowledge_points_corpus_and_usage(self):
        request = CourseKnowledgeCorpusAnalysisRequestObject(
            textSegments=[
                TextSegment(text="课前闲聊。", bg=0, ed=5),
                TextSegment(text="正式讲人工智能工具。", bg=10, ed=20),
                TextSegment(text="课间聊天。", bg=21, ed=30),
                TextSegment(text="继续讲履职能力建设。", bg=31, ed=40),
            ],
            course_start=10,
            course_end=40,
            breaks=[{"start": 20, "end": 31}],
            max_knowledge_points=5,
            max_corpus=5,
        )

        async def fake_chunk_call(chunk):
            self.assertNotIn("课前", chunk["text"])
            self.assertNotIn("课间", chunk["text"])
            return {
                "knowledge_points": [
                    {"title": "人工智能工具", "description": "说明 AI 工具在履职中的辅助作用。"},
                    {"title": "然后", "description": "无意义口头词。"},
                ],
                "corpus": [
                    {
                        "content": "人工智能工具可以辅助材料分析和内容生成。",
                        "description": "说明 AI 工具的履职辅助作用。",
                        "time_range": {"start": 1, "end": 2},
                    }
                ],
            }, {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}

        async def fake_final_call(payload):
            self.assertEqual(payload["max_knowledge_points"], 5)
            self.assertEqual(payload["max_corpus"], 5)
            self.assertEqual(len(payload["knowledge_points"]), 1)
            self.assertEqual(len(payload["corpus"]), 1)
            return {
                "knowledge_points": [
                    {
                        "title": "人工智能工具辅助履职",
                        "description": "说明 AI 工具在材料处理和内容生成中的应用。",
                        "score": 99,
                    }
                ],
                "corpus": [
                    {
                        "content": "人工智能工具可以辅助完成材料分析和内容生成，提高履职效率。",
                        "description": "说明人工智能工具对履职效率的提升。",
                        "confidence": 0.9,
                    }
                ],
            }, {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4}

        data, usage, used_model = await analyze_course_knowledge_corpus(
            request,
            chunk_call=fake_chunk_call,
            final_call=fake_final_call,
            chunk_chars=20,
            chunk_overlap_chars=0,
            max_chunks=4,
            chunk_concurrency=2,
            chunk_retry_attempts=1,
            final_retry_attempts=1,
            chunk_max_knowledge_points=3,
            chunk_max_corpus=3,
            final_max_knowledge_points=10,
            final_max_corpus=10,
            max_description_chars=80,
            max_corpus_content_chars=120,
        )

        self.assertEqual(used_model, "qwen3-8b")
        self.assertEqual(usage.total_tokens, 6)
        self.assertEqual(
            data,
            {
                "knowledge_points": [
                    {"title": "人工智能工具辅助履职", "description": "说明AI工具在材料处理和内容生成中的应用。"}
                ],
                "corpus": [
                    {
                        "content": "人工智能工具可以辅助完成材料分析和内容生成，提高履职效率。",
                        "description": "说明人工智能工具对履职效率的提升。",
                    }
                ],
            },
        )

    async def test_fixture_validation_path_returns_unique_simplified_arrays(self):
        fixture_path = next(
            iter(sorted(Path("tests/text_segments").glob("*.textSegments.json"))),
            None,
        )
        self.assertIsNotNone(fixture_path)
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        request = CourseKnowledgeCorpusAnalysisRequestObject(
            textSegments=[TextSegment(**item) for item in payload["textSegments"]],
            course_start=159.8,
            course_end=8830.0,
            breaks=[{"start": 3794.47, "end": 5867.0}],
            max_knowledge_points=3,
            max_corpus=3,
        )

        async def fake_chunk_call(_chunk):
            return {
                "knowledge_points": [
                    {"title": "人工智能工具", "description": "说明 AI 工具在履职中的辅助作用。"},
                    {"title": "人工智能工具", "description": "重复知识点。"},
                    {"title": "履职能力", "description": "说明岗位职责和工作推动能力。"},
                ],
                "corpus": [
                    {"content": "人工智能工具可以辅助材料分析和内容生成。", "description": "AI 工具应用。"},
                    {"content": "人工智能工具可以辅助材料分析和内容生成。", "description": "重复语料。"},
                    {"content": "履职能力建设需要结合岗位职责和实际问题。", "description": "履职能力建设。"},
                ],
            }, {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}

        async def fake_final_call(payload):
            return {
                "knowledge_points": payload["knowledge_points"],
                "corpus": payload["corpus"],
            }, {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}

        data, _usage, _ = await analyze_course_knowledge_corpus(
            request,
            chunk_call=fake_chunk_call,
            final_call=fake_final_call,
            chunk_chars=500,
            chunk_overlap_chars=60,
            max_chunks=2,
            chunk_concurrency=2,
            chunk_retry_attempts=1,
            final_retry_attempts=1,
            chunk_max_knowledge_points=3,
            chunk_max_corpus=3,
            final_max_knowledge_points=3,
            final_max_corpus=3,
            max_description_chars=80,
            max_corpus_content_chars=120,
        )

        self.assertIsInstance(data["knowledge_points"], list)
        self.assertIsInstance(data["corpus"], list)
        titles = [item["title"] for item in data["knowledge_points"]]
        contents = [item["content"] for item in data["corpus"]]
        self.assertTrue(titles)
        self.assertTrue(contents)
        self.assertEqual(len(titles), len(set(titles)))
        self.assertEqual(len(contents), len(set(contents)))
        forbidden = {
            "hotwords",
            "word",
            "explanation",
            "time_range",
            "time_ranges",
            "occurrences",
            "evidence",
            "score",
            "confidence",
            "filtering",
            "execution",
        }
        for item in data["knowledge_points"] + data["corpus"]:
            self.assertTrue(forbidden.isdisjoint(item.keys()))


class CourseKnowledgeCorpusApiTests(unittest.TestCase):
    def test_api_rejects_empty_text_segments(self):
        from app.main import app

        client = TestClient(app)
        response = client.post(
            "/v1/course_knowledge_corpus",
            json={"textSegments": [], "course_start": 1, "course_end": 2, "breaks": []},
        )

        self.assertEqual(response.status_code, 400)

    def test_api_rejects_missing_text_segments(self):
        from app.main import app

        client = TestClient(app)
        response = client.post(
            "/v1/course_knowledge_corpus",
            json={"course_start": 1, "course_end": 2, "breaks": []},
        )

        self.assertEqual(response.status_code, 400)

    def test_api_rejects_invalid_course_range(self):
        from app.main import app

        client = TestClient(app)
        response = client.post(
            "/v1/course_knowledge_corpus",
            json={
                "textSegments": [{"text": "正式上课。", "bg": 1, "ed": 2}],
                "course_start": 3,
                "course_end": 2,
                "breaks": [],
            },
        )

        self.assertEqual(response.status_code, 400)

    def test_api_returns_simplified_knowledge_corpus_result(self):
        import app.api.v1.routes.course_knowledge_corpus_analysis as route_module
        from app.main import app
        from app.models.entities import UsageInfo

        async def fake_analyze(request):
            self.assertEqual(request.max_knowledge_points, 2)
            self.assertEqual(request.max_corpus, 2)
            return {
                "knowledge_points": [{"title": "人工智能工具", "description": "说明智能工具应用。"}],
                "corpus": [{"content": "人工智能工具可以辅助材料分析。", "description": "AI 工具应用。"}],
            }, UsageInfo(prompt_tokens=1, completion_tokens=1, total_tokens=2), "qwen3-8b"

        old = route_module.analyze_course_knowledge_corpus
        route_module.analyze_course_knowledge_corpus = fake_analyze
        try:
            client = TestClient(app)
            response = client.post(
                "/v1/course_knowledge_corpus",
                json={
                    "textSegments": [{"text": "正式讲人工智能。", "bg": 1, "ed": 2}],
                    "course_start": 1,
                    "course_end": 2,
                    "breaks": [],
                    "max_knowledge_points": 2,
                    "max_corpus": 2,
                },
            )
        finally:
            route_module.analyze_course_knowledge_corpus = old

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["model"], "qwen3-8b")
        self.assertEqual(payload["usage"]["total_tokens"], 2)
        self.assertEqual(set(payload["result"].keys()), {"knowledge_points", "corpus"})
        for item in payload["result"]["knowledge_points"]:
            self.assertEqual(set(item.keys()), {"title", "description"})
        for item in payload["result"]["corpus"]:
            self.assertEqual(set(item.keys()), {"content", "description"})


if __name__ == "__main__":
    unittest.main()
