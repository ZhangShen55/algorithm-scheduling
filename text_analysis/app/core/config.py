import os
from pydantic import BaseModel
from typing import List

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


class _eval_weights(BaseModel):
    base_score: float = 0.0,
    knob: float = 0.0, ## 旋钮用于控制是重强调还是轻强调权重配置
    politics: float = 0.0
    content: float = 0.0
    attitude: float = 0.0
    method: float = 0.0
    effect: float = 0.0

class _Settings(BaseModel):
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = ""
    OPENAI_MODEL: str = ""
    EVAL_WEIGHT: _eval_weights
    CORS_ORIGINS: List[str] = ["*"]
    MT_API_KEY: str = ""
    MT_BASE_URL: str = ""
    MT_MODEL: str = ""
    MT_SEGMENT_SIZE: int = 5
    MT_MAX_CONCURRENCY: int = 128
    MT_QUEUE_TIMEOUT: int = 60
    COURSE_OVERVIEW_SEGMENT_COUNT: int = 4
    COURSE_OVERVIEW_CONCURRENCY: int = 4
    EXTRACT_KNOWLEDGE_V2_ENABLE_SEGMENTATION: bool = True
    EXTRACT_KNOWLEDGE_V2_MAX_TEXT_CHARS: int = 12000
    EXTRACT_KNOWLEDGE_V2_SEGMENT_CHARS: int = 6000
    EXTRACT_KNOWLEDGE_V2_SEGMENT_OVERLAP_CHARS: int = 300
    EXTRACT_KNOWLEDGE_V2_MAX_SEGMENTS: int = 16
    EXTRACT_KNOWLEDGE_V2_CONCURRENCY: int = 4
    EXTRACT_KNOWLEDGE_V2_RETRY_ATTEMPTS: int = 3
    EXTRACT_KNOWLEDGE_V2_SEGMENT_RETRY_ATTEMPTS: int = 2
    EXTRACT_KNOWLEDGE_V2_FALLBACK_SPLIT: bool = True
    EXTRACT_KNOWLEDGE_V2_MIN_TRUE_PER_MODULE: int = 2
    COURSE_TIME_ANALYSIS_ENABLE_LLM_VALIDATION: bool = True
    COURSE_TIME_ANALYSIS_LLM_CONCURRENCY: int = 4
    COURSE_TIME_ANALYSIS_LLM_RETRY_ATTEMPTS: int = 2
    COURSE_TIME_ANALYSIS_CANDIDATE_CONTEXT_BEFORE_SEC: int = 120
    COURSE_TIME_ANALYSIS_CANDIDATE_CONTEXT_AFTER_SEC: int = 120
    COURSE_TIME_ANALYSIS_FALLBACK_WINDOW_SEC: int = 300
    COURSE_TIME_ANALYSIS_MAX_FALLBACK_WINDOWS: int = 12
    COURSE_TIME_ANALYSIS_MAX_LLM_CANDIDATES: int = 24
    COURSE_TIME_ANALYSIS_MIN_BREAK_DURATION_SEC: int = 600
    COURSE_TIME_ANALYSIS_MAX_BREAK_DURATION_SEC: int = 2400
    COURSE_TIME_ANALYSIS_BOUNDARY_SEMANTIC_WINDOW_SEC: int = 1800
    COURSE_TIME_ANALYSIS_BOUNDARY_SEMANTIC_CHUNK_SEC: int = 300
    COURSE_TIME_ANALYSIS_BOUNDARY_SEMANTIC_OVERLAP_SEC: int = 60
    COURSE_TIME_ANALYSIS_BREAK_END_SEARCH_MIN_SEC: int = 300
    COURSE_TIME_ANALYSIS_BREAK_END_SEARCH_MAX_SEC: int = 1800
    COURSE_TIME_ANALYSIS_COURSE_START_CANDIDATE_BUDGET: int = 8
    COURSE_TIME_ANALYSIS_COURSE_END_CANDIDATE_BUDGET: int = 8
    COURSE_TIME_ANALYSIS_BREAK_START_CANDIDATE_BUDGET: int = 12
    COURSE_TIME_ANALYSIS_BREAK_END_CANDIDATE_BUDGET: int = 12
    COURSE_TIME_ANALYSIS_WEAK_CANDIDATE_BUDGET: int = 8
    LANGUAGE_EXPRESSION_ANALYSIS_CHUNK_CHARS: int = 3000
    LANGUAGE_EXPRESSION_ANALYSIS_CHUNK_OVERLAP_CHARS: int = 300
    LANGUAGE_EXPRESSION_ANALYSIS_MAX_CHUNKS: int = 20
    LANGUAGE_EXPRESSION_ANALYSIS_CHUNK_CONCURRENCY: int = 4
    LANGUAGE_EXPRESSION_ANALYSIS_CHUNK_RETRY_ATTEMPTS: int = 2
    LANGUAGE_EXPRESSION_ANALYSIS_FINAL_RETRY_ATTEMPTS: int = 2
    LANGUAGE_EXPRESSION_ANALYSIS_MIN_EFFECTIVE_CHARS: int = 800
    LANGUAGE_EXPRESSION_ANALYSIS_MIN_ADVANTAGES_PER_DIMENSION: int = 2
    LANGUAGE_EXPRESSION_ANALYSIS_MIN_PROBLEMS_PER_DIMENSION: int = 0
    LANGUAGE_EXPRESSION_ANALYSIS_MAX_ITEMS_PER_DIMENSION: int = 4
    LANGUAGE_EXPRESSION_ANALYSIS_DEFAULT_TEMPERATURE: float = 0.6
    LANGUAGE_EXPRESSION_ANALYSIS_OVERALL_SCORE_MIN: int = 0
    LANGUAGE_EXPRESSION_ANALYSIS_OVERALL_SCORE_MAX: int = 100
    LANGUAGE_EXPRESSION_ANALYSIS_ENABLE_AUTO_COURSE_TIME_ANALYSIS: bool = True
    LANGUAGE_EXPRESSION_ANALYSIS_ENABLE_FINAL_LLM_POLISH: bool = True
    COURSE_KNOWLEDGE_CORPUS_ANALYSIS_CHUNK_CHARS: int = 500
    COURSE_KNOWLEDGE_CORPUS_ANALYSIS_CHUNK_OVERLAP_CHARS: int = 60
    COURSE_KNOWLEDGE_CORPUS_ANALYSIS_MAX_CHUNKS: int = 40
    COURSE_KNOWLEDGE_CORPUS_ANALYSIS_CHUNK_CONCURRENCY: int = 4
    COURSE_KNOWLEDGE_CORPUS_ANALYSIS_CHUNK_RETRY_ATTEMPTS: int = 2
    COURSE_KNOWLEDGE_CORPUS_ANALYSIS_FINAL_RETRY_ATTEMPTS: int = 2
    COURSE_KNOWLEDGE_CORPUS_ANALYSIS_CHUNK_MAX_KNOWLEDGE_POINTS: int = 3
    COURSE_KNOWLEDGE_CORPUS_ANALYSIS_CHUNK_MAX_CORPUS: int = 3
    COURSE_KNOWLEDGE_CORPUS_ANALYSIS_FINAL_MAX_KNOWLEDGE_POINTS: int = 20
    COURSE_KNOWLEDGE_CORPUS_ANALYSIS_FINAL_MAX_CORPUS: int = 20
    COURSE_KNOWLEDGE_CORPUS_ANALYSIS_MAX_DESCRIPTION_CHARS: int = 120
    COURSE_KNOWLEDGE_CORPUS_ANALYSIS_MAX_CORPUS_CONTENT_CHARS: int = 240
    STUDENT_INTERACTION_ANALYSIS_CHUNK_CHARS: int = 2000
    STUDENT_INTERACTION_ANALYSIS_CHUNK_OVERLAP_CHARS: int = 150
    STUDENT_INTERACTION_ANALYSIS_CHUNK_CONCURRENCY: int = 4
    STUDENT_INTERACTION_ANALYSIS_CHUNK_RETRY_ATTEMPTS: int = 2
    STUDENT_INTERACTION_ANALYSIS_VERIFY_CONTEXT_SECONDS: int = 30
    STUDENT_INTERACTION_ANALYSIS_VERIFY_RETRY_ATTEMPTS: int = 2
    STUDENT_INTERACTION_ANALYSIS_MERGE_GAP_SECONDS: int = 30
    STUDENT_INTERACTION_ANALYSIS_MAX_CHUNKS: int = 80
    STUDENT_INTERACTION_ANALYSIS_MAX_CANDIDATES_PER_CHUNK: int = 3
    QUESTION_CLASSIFICATION_LLM_CONCURRENCY: int = 4


def _positive_int(value, default: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return n if n > 0 else default


def _non_negative_int(value, default: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return n if n >= 0 else default


def _bool_value(value, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _float_value(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _load_from_config_file(cfg_path: str) -> dict:
    if os.path.exists(cfg_path):
        with open(cfg_path, "rb") as f:
            data = tomllib.load(f)
        course_overview = data.get("course_overview", {}) or {}
        extract_knowledge_v2 = data.get("extract_knowledge_v2", {}) or {}
        course_time_analysis = data.get("course_time_analysis", {}) or {}
        language_expression_analysis = data.get("language_expression_analysis", {}) or {}
        course_knowledge_corpus_analysis = data.get("course_knowledge_corpus_analysis", {}) or {}
        student_interaction_analysis = data.get("student_interaction_analysis", {}) or {}
        question_classification = data.get("question_classification", {}) or {}
        return {
            "OPENAI_API_KEY": data.get("api_key", ""),
            "OPENAI_BASE_URL": data.get("base_url", ""),
            "OPENAI_MODEL": data.get("model", ""),
            "EVAL_WEIGHT": data.get("eval_weight", {}),
            "MT_API_KEY": data.get("mt_api_key", ""),
            "MT_BASE_URL": data.get("mt_base_url", ""),
            "MT_MODEL": data.get("mt_model", ""),
            "MT_SEGMENT_SIZE": _positive_int(data.get("segment_size"), 5),
            "MT_MAX_CONCURRENCY": _positive_int(data.get("mt_max_concurrency"), 128),
            "MT_QUEUE_TIMEOUT": _positive_int(data.get("mt_queue_timeout"), 60),
            "COURSE_OVERVIEW_SEGMENT_COUNT": _positive_int(course_overview.get("segment_count"), 4),
            "COURSE_OVERVIEW_CONCURRENCY": _positive_int(
                course_overview.get("concurrency"),
                _positive_int(course_overview.get("segment_count"), 4),
            ),
            "EXTRACT_KNOWLEDGE_V2_ENABLE_SEGMENTATION": _bool_value(
                extract_knowledge_v2.get("enable_segmentation"),
                True,
            ),
            "EXTRACT_KNOWLEDGE_V2_MAX_TEXT_CHARS": _positive_int(extract_knowledge_v2.get("max_text_chars"), 12000),
            "EXTRACT_KNOWLEDGE_V2_SEGMENT_CHARS": _positive_int(extract_knowledge_v2.get("segment_chars"), 6000),
            "EXTRACT_KNOWLEDGE_V2_SEGMENT_OVERLAP_CHARS": _positive_int(
                extract_knowledge_v2.get("segment_overlap_chars"),
                300,
            ),
            "EXTRACT_KNOWLEDGE_V2_MAX_SEGMENTS": _positive_int(extract_knowledge_v2.get("max_segments"), 16),
            "EXTRACT_KNOWLEDGE_V2_CONCURRENCY": _positive_int(extract_knowledge_v2.get("concurrency"), 4),
            "EXTRACT_KNOWLEDGE_V2_RETRY_ATTEMPTS": _positive_int(extract_knowledge_v2.get("retry_attempts"), 3),
            "EXTRACT_KNOWLEDGE_V2_SEGMENT_RETRY_ATTEMPTS": _positive_int(
                extract_knowledge_v2.get("segment_retry_attempts"),
                2,
            ),
            "EXTRACT_KNOWLEDGE_V2_FALLBACK_SPLIT": _bool_value(extract_knowledge_v2.get("fallback_split"), True),
            "EXTRACT_KNOWLEDGE_V2_MIN_TRUE_PER_MODULE": _positive_int(
                extract_knowledge_v2.get("min_true_per_module"),
                2,
            ),
            "COURSE_TIME_ANALYSIS_ENABLE_LLM_VALIDATION": _bool_value(
                course_time_analysis.get("enable_llm_validation"),
                True,
            ),
            "COURSE_TIME_ANALYSIS_LLM_CONCURRENCY": _positive_int(course_time_analysis.get("llm_concurrency"), 4),
            "COURSE_TIME_ANALYSIS_LLM_RETRY_ATTEMPTS": _positive_int(
                course_time_analysis.get("llm_retry_attempts"),
                2,
            ),
            "COURSE_TIME_ANALYSIS_CANDIDATE_CONTEXT_BEFORE_SEC": _positive_int(
                course_time_analysis.get("candidate_context_before_sec"),
                120,
            ),
            "COURSE_TIME_ANALYSIS_CANDIDATE_CONTEXT_AFTER_SEC": _positive_int(
                course_time_analysis.get("candidate_context_after_sec"),
                120,
            ),
            "COURSE_TIME_ANALYSIS_FALLBACK_WINDOW_SEC": _positive_int(
                course_time_analysis.get("fallback_window_sec"),
                300,
            ),
            "COURSE_TIME_ANALYSIS_MAX_FALLBACK_WINDOWS": _positive_int(
                course_time_analysis.get("max_fallback_windows"),
                12,
            ),
            "COURSE_TIME_ANALYSIS_MAX_LLM_CANDIDATES": _positive_int(
                course_time_analysis.get("max_llm_candidates"),
                24,
            ),
            "COURSE_TIME_ANALYSIS_MIN_BREAK_DURATION_SEC": _positive_int(
                course_time_analysis.get("min_break_duration_sec"),
                600,
            ),
            "COURSE_TIME_ANALYSIS_MAX_BREAK_DURATION_SEC": _positive_int(
                course_time_analysis.get("max_break_duration_sec"),
                2400,
            ),
            "COURSE_TIME_ANALYSIS_BOUNDARY_SEMANTIC_WINDOW_SEC": _positive_int(
                course_time_analysis.get("boundary_semantic_window_sec"),
                1800,
            ),
            "COURSE_TIME_ANALYSIS_BOUNDARY_SEMANTIC_CHUNK_SEC": _positive_int(
                course_time_analysis.get("boundary_semantic_chunk_sec"),
                300,
            ),
            "COURSE_TIME_ANALYSIS_BOUNDARY_SEMANTIC_OVERLAP_SEC": _positive_int(
                course_time_analysis.get("boundary_semantic_overlap_sec"),
                60,
            ),
            "COURSE_TIME_ANALYSIS_BREAK_END_SEARCH_MIN_SEC": _positive_int(
                course_time_analysis.get("break_end_search_min_sec"),
                300,
            ),
            "COURSE_TIME_ANALYSIS_BREAK_END_SEARCH_MAX_SEC": _positive_int(
                course_time_analysis.get("break_end_search_max_sec"),
                1800,
            ),
            "COURSE_TIME_ANALYSIS_COURSE_START_CANDIDATE_BUDGET": _positive_int(
                course_time_analysis.get("course_start_candidate_budget"),
                8,
            ),
            "COURSE_TIME_ANALYSIS_COURSE_END_CANDIDATE_BUDGET": _positive_int(
                course_time_analysis.get("course_end_candidate_budget"),
                8,
            ),
            "COURSE_TIME_ANALYSIS_BREAK_START_CANDIDATE_BUDGET": _positive_int(
                course_time_analysis.get("break_start_candidate_budget"),
                12,
            ),
            "COURSE_TIME_ANALYSIS_BREAK_END_CANDIDATE_BUDGET": _positive_int(
                course_time_analysis.get("break_end_candidate_budget"),
                12,
            ),
            "COURSE_TIME_ANALYSIS_WEAK_CANDIDATE_BUDGET": _positive_int(
                course_time_analysis.get("weak_candidate_budget"),
                8,
            ),
            "LANGUAGE_EXPRESSION_ANALYSIS_CHUNK_CHARS": _positive_int(
                language_expression_analysis.get("chunk_chars"),
                5000,
            ),
            "LANGUAGE_EXPRESSION_ANALYSIS_CHUNK_OVERLAP_CHARS": _positive_int(
                language_expression_analysis.get("chunk_overlap_chars"),
                300,
            ),
            "LANGUAGE_EXPRESSION_ANALYSIS_MAX_CHUNKS": _positive_int(
                language_expression_analysis.get("max_chunks"),
                20,
            ),
            "LANGUAGE_EXPRESSION_ANALYSIS_CHUNK_CONCURRENCY": _positive_int(
                language_expression_analysis.get("chunk_concurrency"),
                4,
            ),
            "LANGUAGE_EXPRESSION_ANALYSIS_CHUNK_RETRY_ATTEMPTS": _positive_int(
                language_expression_analysis.get("chunk_retry_attempts"),
                2,
            ),
            "LANGUAGE_EXPRESSION_ANALYSIS_FINAL_RETRY_ATTEMPTS": _positive_int(
                language_expression_analysis.get("final_retry_attempts"),
                2,
            ),
            "LANGUAGE_EXPRESSION_ANALYSIS_MIN_EFFECTIVE_CHARS": _positive_int(
                language_expression_analysis.get("min_effective_chars"),
                800,
            ),
            "LANGUAGE_EXPRESSION_ANALYSIS_MIN_ADVANTAGES_PER_DIMENSION": _positive_int(
                language_expression_analysis.get("min_advantages_per_dimension"),
                2,
            ),
            "LANGUAGE_EXPRESSION_ANALYSIS_MIN_PROBLEMS_PER_DIMENSION": _non_negative_int(
                language_expression_analysis.get("min_problems_per_dimension"),
                0,
            ),
            "LANGUAGE_EXPRESSION_ANALYSIS_MAX_ITEMS_PER_DIMENSION": _positive_int(
                language_expression_analysis.get("max_items_per_dimension"),
                4,
            ),
            "LANGUAGE_EXPRESSION_ANALYSIS_DEFAULT_TEMPERATURE": _float_value(
                language_expression_analysis.get("default_temperature"),
                0.6,
            ),
            "LANGUAGE_EXPRESSION_ANALYSIS_OVERALL_SCORE_MIN": _positive_int(
                language_expression_analysis.get("overall_score_min"),
                0,
            ),
            "LANGUAGE_EXPRESSION_ANALYSIS_OVERALL_SCORE_MAX": _positive_int(
                language_expression_analysis.get("overall_score_max"),
                100,
            ),
            "LANGUAGE_EXPRESSION_ANALYSIS_ENABLE_AUTO_COURSE_TIME_ANALYSIS": _bool_value(
                language_expression_analysis.get("enable_auto_course_time_analysis"),
                True,
            ),
            "LANGUAGE_EXPRESSION_ANALYSIS_ENABLE_FINAL_LLM_POLISH": _bool_value(
                language_expression_analysis.get("enable_final_llm_polish"),
                True,
            ),
            "COURSE_KNOWLEDGE_CORPUS_ANALYSIS_CHUNK_CHARS": _positive_int(
                course_knowledge_corpus_analysis.get("chunk_chars"),
                500,
            ),
            "COURSE_KNOWLEDGE_CORPUS_ANALYSIS_CHUNK_OVERLAP_CHARS": _non_negative_int(
                course_knowledge_corpus_analysis.get("chunk_overlap_chars"),
                60,
            ),
            "COURSE_KNOWLEDGE_CORPUS_ANALYSIS_MAX_CHUNKS": _positive_int(
                course_knowledge_corpus_analysis.get("max_chunks"),
                40,
            ),
            "COURSE_KNOWLEDGE_CORPUS_ANALYSIS_CHUNK_CONCURRENCY": _positive_int(
                course_knowledge_corpus_analysis.get("chunk_concurrency"),
                4,
            ),
            "COURSE_KNOWLEDGE_CORPUS_ANALYSIS_CHUNK_RETRY_ATTEMPTS": _positive_int(
                course_knowledge_corpus_analysis.get("chunk_retry_attempts"),
                2,
            ),
            "COURSE_KNOWLEDGE_CORPUS_ANALYSIS_FINAL_RETRY_ATTEMPTS": _positive_int(
                course_knowledge_corpus_analysis.get("final_retry_attempts"),
                2,
            ),
            "COURSE_KNOWLEDGE_CORPUS_ANALYSIS_CHUNK_MAX_KNOWLEDGE_POINTS": _positive_int(
                course_knowledge_corpus_analysis.get("chunk_max_knowledge_points"),
                3,
            ),
            "COURSE_KNOWLEDGE_CORPUS_ANALYSIS_CHUNK_MAX_CORPUS": _positive_int(
                course_knowledge_corpus_analysis.get("chunk_max_corpus"),
                3,
            ),
            "COURSE_KNOWLEDGE_CORPUS_ANALYSIS_FINAL_MAX_KNOWLEDGE_POINTS": _positive_int(
                course_knowledge_corpus_analysis.get("final_max_knowledge_points"),
                20,
            ),
            "COURSE_KNOWLEDGE_CORPUS_ANALYSIS_FINAL_MAX_CORPUS": _positive_int(
                course_knowledge_corpus_analysis.get("final_max_corpus"),
                20,
            ),
            "COURSE_KNOWLEDGE_CORPUS_ANALYSIS_MAX_DESCRIPTION_CHARS": _positive_int(
                course_knowledge_corpus_analysis.get("max_description_chars"),
                120,
            ),
            "COURSE_KNOWLEDGE_CORPUS_ANALYSIS_MAX_CORPUS_CONTENT_CHARS": _positive_int(
                course_knowledge_corpus_analysis.get("max_corpus_content_chars"),
                240,
            ),
            "STUDENT_INTERACTION_ANALYSIS_CHUNK_CHARS": _positive_int(
                student_interaction_analysis.get("chunk_chars"),
                2000,
            ),
            "STUDENT_INTERACTION_ANALYSIS_CHUNK_OVERLAP_CHARS": _non_negative_int(
                student_interaction_analysis.get("chunk_overlap_chars"),
                150,
            ),
            "STUDENT_INTERACTION_ANALYSIS_CHUNK_CONCURRENCY": _positive_int(
                student_interaction_analysis.get("chunk_concurrency"),
                4,
            ),
            "STUDENT_INTERACTION_ANALYSIS_CHUNK_RETRY_ATTEMPTS": _positive_int(
                student_interaction_analysis.get("chunk_retry_attempts"),
                2,
            ),
            "STUDENT_INTERACTION_ANALYSIS_VERIFY_CONTEXT_SECONDS": _positive_int(
                student_interaction_analysis.get("verify_context_seconds"),
                30,
            ),
            "STUDENT_INTERACTION_ANALYSIS_VERIFY_RETRY_ATTEMPTS": _positive_int(
                student_interaction_analysis.get("verify_retry_attempts"),
                2,
            ),
            "STUDENT_INTERACTION_ANALYSIS_MERGE_GAP_SECONDS": _positive_int(
                student_interaction_analysis.get("merge_gap_seconds"),
                30,
            ),
            "STUDENT_INTERACTION_ANALYSIS_MAX_CHUNKS": _positive_int(
                student_interaction_analysis.get("max_chunks"),
                80,
            ),
            "STUDENT_INTERACTION_ANALYSIS_MAX_CANDIDATES_PER_CHUNK": _positive_int(
                student_interaction_analysis.get("max_candidates_per_chunk"),
                3,
            ),
            "QUESTION_CLASSIFICATION_LLM_CONCURRENCY": _positive_int(
                question_classification.get("llm_concurrency"),
                4,
            ),
        }
    return {}


_cfg = _load_from_config_file(os.getenv("CONFIG_PATH", "config.toml"))

_course_overview_segment_count = _positive_int(
    os.getenv("COURSE_OVERVIEW_SEGMENT_COUNT", _cfg.get("COURSE_OVERVIEW_SEGMENT_COUNT", 4)),
    4,
)
_course_overview_concurrency = _positive_int(
    os.getenv("COURSE_OVERVIEW_CONCURRENCY", _cfg.get("COURSE_OVERVIEW_CONCURRENCY", _course_overview_segment_count)),
    _course_overview_segment_count,
)
_extract_knowledge_v2_enable_segmentation = _bool_value(
    os.getenv("EXTRACT_KNOWLEDGE_V2_ENABLE_SEGMENTATION", _cfg.get("EXTRACT_KNOWLEDGE_V2_ENABLE_SEGMENTATION", True)),
    True,
)

settings = _Settings(
    OPENAI_API_KEY=os.getenv("OPENAI_API_KEY", _cfg.get("OPENAI_API_KEY", "")),
    OPENAI_BASE_URL=os.getenv("OPENAI_BASE_URL", _cfg.get("OPENAI_BASE_URL", "")),
    OPENAI_MODEL=os.getenv("OPENAI_MODEL", _cfg.get("OPENAI_MODEL", "gpt-4o-mini")),
    EVAL_WEIGHT=_eval_weights(**_cfg.get("EVAL_WEIGHT", {})),
    MT_API_KEY=os.getenv("MT_API_KEY", _cfg.get("MT_API_KEY", "")),
    MT_BASE_URL=os.getenv("MT_BASE_URL", _cfg.get("MT_BASE_URL", "")),
    MT_MODEL=os.getenv("MT_MODEL", _cfg.get("MT_MODEL", "")),
    MT_SEGMENT_SIZE=_positive_int(os.getenv("MT_SEGMENT_SIZE", _cfg.get("MT_SEGMENT_SIZE", 5)), 5),
    MT_MAX_CONCURRENCY=_positive_int(os.getenv("MT_MAX_CONCURRENCY", _cfg.get("MT_MAX_CONCURRENCY", 128)), 128),
    MT_QUEUE_TIMEOUT=_positive_int(os.getenv("MT_QUEUE_TIMEOUT", _cfg.get("MT_QUEUE_TIMEOUT", 60)), 60),
    COURSE_OVERVIEW_SEGMENT_COUNT=_course_overview_segment_count,
    COURSE_OVERVIEW_CONCURRENCY=_course_overview_concurrency,
    EXTRACT_KNOWLEDGE_V2_ENABLE_SEGMENTATION=_extract_knowledge_v2_enable_segmentation,
    EXTRACT_KNOWLEDGE_V2_MAX_TEXT_CHARS=_positive_int(
        os.getenv("EXTRACT_KNOWLEDGE_V2_MAX_TEXT_CHARS", _cfg.get("EXTRACT_KNOWLEDGE_V2_MAX_TEXT_CHARS", 12000)),
        12000,
    ),
    EXTRACT_KNOWLEDGE_V2_SEGMENT_CHARS=_positive_int(
        os.getenv("EXTRACT_KNOWLEDGE_V2_SEGMENT_CHARS", _cfg.get("EXTRACT_KNOWLEDGE_V2_SEGMENT_CHARS", 6000)),
        6000,
    ),
    EXTRACT_KNOWLEDGE_V2_SEGMENT_OVERLAP_CHARS=_positive_int(
        os.getenv(
            "EXTRACT_KNOWLEDGE_V2_SEGMENT_OVERLAP_CHARS",
            _cfg.get("EXTRACT_KNOWLEDGE_V2_SEGMENT_OVERLAP_CHARS", 300),
        ),
        300,
    ),
    EXTRACT_KNOWLEDGE_V2_MAX_SEGMENTS=_positive_int(
        os.getenv("EXTRACT_KNOWLEDGE_V2_MAX_SEGMENTS", _cfg.get("EXTRACT_KNOWLEDGE_V2_MAX_SEGMENTS", 16)),
        16,
    ),
    EXTRACT_KNOWLEDGE_V2_CONCURRENCY=_positive_int(
        os.getenv("EXTRACT_KNOWLEDGE_V2_CONCURRENCY", _cfg.get("EXTRACT_KNOWLEDGE_V2_CONCURRENCY", 4)),
        4,
    ),
    EXTRACT_KNOWLEDGE_V2_RETRY_ATTEMPTS=_positive_int(
        os.getenv("EXTRACT_KNOWLEDGE_V2_RETRY_ATTEMPTS", _cfg.get("EXTRACT_KNOWLEDGE_V2_RETRY_ATTEMPTS", 3)),
        3,
    ),
    EXTRACT_KNOWLEDGE_V2_SEGMENT_RETRY_ATTEMPTS=_positive_int(
        os.getenv(
            "EXTRACT_KNOWLEDGE_V2_SEGMENT_RETRY_ATTEMPTS",
            _cfg.get("EXTRACT_KNOWLEDGE_V2_SEGMENT_RETRY_ATTEMPTS", 2),
        ),
        2,
    ),
    EXTRACT_KNOWLEDGE_V2_FALLBACK_SPLIT=_bool_value(
        os.getenv("EXTRACT_KNOWLEDGE_V2_FALLBACK_SPLIT", _cfg.get("EXTRACT_KNOWLEDGE_V2_FALLBACK_SPLIT", True)),
        True,
    ),
    EXTRACT_KNOWLEDGE_V2_MIN_TRUE_PER_MODULE=_positive_int(
        os.getenv(
            "EXTRACT_KNOWLEDGE_V2_MIN_TRUE_PER_MODULE",
            _cfg.get("EXTRACT_KNOWLEDGE_V2_MIN_TRUE_PER_MODULE", 2),
        ),
        2,
    ),
    COURSE_TIME_ANALYSIS_ENABLE_LLM_VALIDATION=_bool_value(
        os.getenv(
            "COURSE_TIME_ANALYSIS_ENABLE_LLM_VALIDATION",
            _cfg.get("COURSE_TIME_ANALYSIS_ENABLE_LLM_VALIDATION", True),
        ),
        True,
    ),
    COURSE_TIME_ANALYSIS_LLM_CONCURRENCY=_positive_int(
        os.getenv("COURSE_TIME_ANALYSIS_LLM_CONCURRENCY", _cfg.get("COURSE_TIME_ANALYSIS_LLM_CONCURRENCY", 4)),
        4,
    ),
    COURSE_TIME_ANALYSIS_LLM_RETRY_ATTEMPTS=_positive_int(
        os.getenv(
            "COURSE_TIME_ANALYSIS_LLM_RETRY_ATTEMPTS",
            _cfg.get("COURSE_TIME_ANALYSIS_LLM_RETRY_ATTEMPTS", 2),
        ),
        2,
    ),
    COURSE_TIME_ANALYSIS_CANDIDATE_CONTEXT_BEFORE_SEC=_positive_int(
        os.getenv(
            "COURSE_TIME_ANALYSIS_CANDIDATE_CONTEXT_BEFORE_SEC",
            _cfg.get("COURSE_TIME_ANALYSIS_CANDIDATE_CONTEXT_BEFORE_SEC", 120),
        ),
        120,
    ),
    COURSE_TIME_ANALYSIS_CANDIDATE_CONTEXT_AFTER_SEC=_positive_int(
        os.getenv(
            "COURSE_TIME_ANALYSIS_CANDIDATE_CONTEXT_AFTER_SEC",
            _cfg.get("COURSE_TIME_ANALYSIS_CANDIDATE_CONTEXT_AFTER_SEC", 120),
        ),
        120,
    ),
    COURSE_TIME_ANALYSIS_FALLBACK_WINDOW_SEC=_positive_int(
        os.getenv(
            "COURSE_TIME_ANALYSIS_FALLBACK_WINDOW_SEC",
            _cfg.get("COURSE_TIME_ANALYSIS_FALLBACK_WINDOW_SEC", 300),
        ),
        300,
    ),
    COURSE_TIME_ANALYSIS_MAX_FALLBACK_WINDOWS=_positive_int(
        os.getenv(
            "COURSE_TIME_ANALYSIS_MAX_FALLBACK_WINDOWS",
            _cfg.get("COURSE_TIME_ANALYSIS_MAX_FALLBACK_WINDOWS", 12),
        ),
        12,
    ),
    COURSE_TIME_ANALYSIS_MAX_LLM_CANDIDATES=_positive_int(
        os.getenv(
            "COURSE_TIME_ANALYSIS_MAX_LLM_CANDIDATES",
            _cfg.get("COURSE_TIME_ANALYSIS_MAX_LLM_CANDIDATES", 24),
        ),
        24,
    ),
    COURSE_TIME_ANALYSIS_MIN_BREAK_DURATION_SEC=_positive_int(
        os.getenv(
            "COURSE_TIME_ANALYSIS_MIN_BREAK_DURATION_SEC",
            _cfg.get("COURSE_TIME_ANALYSIS_MIN_BREAK_DURATION_SEC", 600),
        ),
        600,
    ),
    COURSE_TIME_ANALYSIS_MAX_BREAK_DURATION_SEC=_positive_int(
        os.getenv(
            "COURSE_TIME_ANALYSIS_MAX_BREAK_DURATION_SEC",
            _cfg.get("COURSE_TIME_ANALYSIS_MAX_BREAK_DURATION_SEC", 2400),
        ),
        2400,
    ),
    COURSE_TIME_ANALYSIS_BOUNDARY_SEMANTIC_WINDOW_SEC=_positive_int(
        os.getenv(
            "COURSE_TIME_ANALYSIS_BOUNDARY_SEMANTIC_WINDOW_SEC",
            _cfg.get("COURSE_TIME_ANALYSIS_BOUNDARY_SEMANTIC_WINDOW_SEC", 1800),
        ),
        1800,
    ),
    COURSE_TIME_ANALYSIS_BOUNDARY_SEMANTIC_CHUNK_SEC=_positive_int(
        os.getenv(
            "COURSE_TIME_ANALYSIS_BOUNDARY_SEMANTIC_CHUNK_SEC",
            _cfg.get("COURSE_TIME_ANALYSIS_BOUNDARY_SEMANTIC_CHUNK_SEC", 300),
        ),
        300,
    ),
    COURSE_TIME_ANALYSIS_BOUNDARY_SEMANTIC_OVERLAP_SEC=_positive_int(
        os.getenv(
            "COURSE_TIME_ANALYSIS_BOUNDARY_SEMANTIC_OVERLAP_SEC",
            _cfg.get("COURSE_TIME_ANALYSIS_BOUNDARY_SEMANTIC_OVERLAP_SEC", 60),
        ),
        60,
    ),
    COURSE_TIME_ANALYSIS_BREAK_END_SEARCH_MIN_SEC=_positive_int(
        os.getenv(
            "COURSE_TIME_ANALYSIS_BREAK_END_SEARCH_MIN_SEC",
            _cfg.get("COURSE_TIME_ANALYSIS_BREAK_END_SEARCH_MIN_SEC", 300),
        ),
        300,
    ),
    COURSE_TIME_ANALYSIS_BREAK_END_SEARCH_MAX_SEC=_positive_int(
        os.getenv(
            "COURSE_TIME_ANALYSIS_BREAK_END_SEARCH_MAX_SEC",
            _cfg.get("COURSE_TIME_ANALYSIS_BREAK_END_SEARCH_MAX_SEC", 1800),
        ),
        1800,
    ),
    COURSE_TIME_ANALYSIS_COURSE_START_CANDIDATE_BUDGET=_positive_int(
        os.getenv(
            "COURSE_TIME_ANALYSIS_COURSE_START_CANDIDATE_BUDGET",
            _cfg.get("COURSE_TIME_ANALYSIS_COURSE_START_CANDIDATE_BUDGET", 8),
        ),
        8,
    ),
    COURSE_TIME_ANALYSIS_COURSE_END_CANDIDATE_BUDGET=_positive_int(
        os.getenv(
            "COURSE_TIME_ANALYSIS_COURSE_END_CANDIDATE_BUDGET",
            _cfg.get("COURSE_TIME_ANALYSIS_COURSE_END_CANDIDATE_BUDGET", 8),
        ),
        8,
    ),
    COURSE_TIME_ANALYSIS_BREAK_START_CANDIDATE_BUDGET=_positive_int(
        os.getenv(
            "COURSE_TIME_ANALYSIS_BREAK_START_CANDIDATE_BUDGET",
            _cfg.get("COURSE_TIME_ANALYSIS_BREAK_START_CANDIDATE_BUDGET", 12),
        ),
        12,
    ),
    COURSE_TIME_ANALYSIS_BREAK_END_CANDIDATE_BUDGET=_positive_int(
        os.getenv(
            "COURSE_TIME_ANALYSIS_BREAK_END_CANDIDATE_BUDGET",
            _cfg.get("COURSE_TIME_ANALYSIS_BREAK_END_CANDIDATE_BUDGET", 12),
        ),
        12,
    ),
    COURSE_TIME_ANALYSIS_WEAK_CANDIDATE_BUDGET=_positive_int(
        os.getenv(
            "COURSE_TIME_ANALYSIS_WEAK_CANDIDATE_BUDGET",
            _cfg.get("COURSE_TIME_ANALYSIS_WEAK_CANDIDATE_BUDGET", 8),
        ),
        8,
    ),
    LANGUAGE_EXPRESSION_ANALYSIS_CHUNK_CHARS=_positive_int(
        os.getenv(
            "LANGUAGE_EXPRESSION_ANALYSIS_CHUNK_CHARS",
            _cfg.get("LANGUAGE_EXPRESSION_ANALYSIS_CHUNK_CHARS", 5000),
        ),
        5000,
    ),
    LANGUAGE_EXPRESSION_ANALYSIS_CHUNK_OVERLAP_CHARS=_positive_int(
        os.getenv(
            "LANGUAGE_EXPRESSION_ANALYSIS_CHUNK_OVERLAP_CHARS",
            _cfg.get("LANGUAGE_EXPRESSION_ANALYSIS_CHUNK_OVERLAP_CHARS", 300),
        ),
        300,
    ),
    LANGUAGE_EXPRESSION_ANALYSIS_MAX_CHUNKS=_positive_int(
        os.getenv(
            "LANGUAGE_EXPRESSION_ANALYSIS_MAX_CHUNKS",
            _cfg.get("LANGUAGE_EXPRESSION_ANALYSIS_MAX_CHUNKS", 20),
        ),
        20,
    ),
    LANGUAGE_EXPRESSION_ANALYSIS_CHUNK_CONCURRENCY=_positive_int(
        os.getenv(
            "LANGUAGE_EXPRESSION_ANALYSIS_CHUNK_CONCURRENCY",
            _cfg.get("LANGUAGE_EXPRESSION_ANALYSIS_CHUNK_CONCURRENCY", 4),
        ),
        4,
    ),
    LANGUAGE_EXPRESSION_ANALYSIS_CHUNK_RETRY_ATTEMPTS=_positive_int(
        os.getenv(
            "LANGUAGE_EXPRESSION_ANALYSIS_CHUNK_RETRY_ATTEMPTS",
            _cfg.get("LANGUAGE_EXPRESSION_ANALYSIS_CHUNK_RETRY_ATTEMPTS", 2),
        ),
        2,
    ),
    LANGUAGE_EXPRESSION_ANALYSIS_FINAL_RETRY_ATTEMPTS=_positive_int(
        os.getenv(
            "LANGUAGE_EXPRESSION_ANALYSIS_FINAL_RETRY_ATTEMPTS",
            _cfg.get("LANGUAGE_EXPRESSION_ANALYSIS_FINAL_RETRY_ATTEMPTS", 2),
        ),
        2,
    ),
    LANGUAGE_EXPRESSION_ANALYSIS_MIN_EFFECTIVE_CHARS=_positive_int(
        os.getenv(
            "LANGUAGE_EXPRESSION_ANALYSIS_MIN_EFFECTIVE_CHARS",
            _cfg.get("LANGUAGE_EXPRESSION_ANALYSIS_MIN_EFFECTIVE_CHARS", 800),
        ),
        800,
    ),
    LANGUAGE_EXPRESSION_ANALYSIS_MIN_ADVANTAGES_PER_DIMENSION=_positive_int(
        os.getenv(
            "LANGUAGE_EXPRESSION_ANALYSIS_MIN_ADVANTAGES_PER_DIMENSION",
            _cfg.get("LANGUAGE_EXPRESSION_ANALYSIS_MIN_ADVANTAGES_PER_DIMENSION", 2),
        ),
        2,
    ),
    LANGUAGE_EXPRESSION_ANALYSIS_MIN_PROBLEMS_PER_DIMENSION=_non_negative_int(
        os.getenv(
            "LANGUAGE_EXPRESSION_ANALYSIS_MIN_PROBLEMS_PER_DIMENSION",
            _cfg.get("LANGUAGE_EXPRESSION_ANALYSIS_MIN_PROBLEMS_PER_DIMENSION", 0),
        ),
        0,
    ),
    LANGUAGE_EXPRESSION_ANALYSIS_MAX_ITEMS_PER_DIMENSION=_positive_int(
        os.getenv(
            "LANGUAGE_EXPRESSION_ANALYSIS_MAX_ITEMS_PER_DIMENSION",
            _cfg.get("LANGUAGE_EXPRESSION_ANALYSIS_MAX_ITEMS_PER_DIMENSION", 4),
        ),
        4,
    ),
    LANGUAGE_EXPRESSION_ANALYSIS_DEFAULT_TEMPERATURE=_float_value(
        os.getenv(
            "LANGUAGE_EXPRESSION_ANALYSIS_DEFAULT_TEMPERATURE",
            _cfg.get("LANGUAGE_EXPRESSION_ANALYSIS_DEFAULT_TEMPERATURE", 0.6),
        ),
        0.6,
    ),
    LANGUAGE_EXPRESSION_ANALYSIS_OVERALL_SCORE_MIN=_positive_int(
        os.getenv(
            "LANGUAGE_EXPRESSION_ANALYSIS_OVERALL_SCORE_MIN",
            _cfg.get("LANGUAGE_EXPRESSION_ANALYSIS_OVERALL_SCORE_MIN", 0),
        ),
        0,
    ),
    LANGUAGE_EXPRESSION_ANALYSIS_OVERALL_SCORE_MAX=_positive_int(
        os.getenv(
            "LANGUAGE_EXPRESSION_ANALYSIS_OVERALL_SCORE_MAX",
            _cfg.get("LANGUAGE_EXPRESSION_ANALYSIS_OVERALL_SCORE_MAX", 100),
        ),
        100,
    ),
    LANGUAGE_EXPRESSION_ANALYSIS_ENABLE_AUTO_COURSE_TIME_ANALYSIS=_bool_value(
        os.getenv(
            "LANGUAGE_EXPRESSION_ANALYSIS_ENABLE_AUTO_COURSE_TIME_ANALYSIS",
            _cfg.get("LANGUAGE_EXPRESSION_ANALYSIS_ENABLE_AUTO_COURSE_TIME_ANALYSIS", True),
        ),
        True,
    ),
    LANGUAGE_EXPRESSION_ANALYSIS_ENABLE_FINAL_LLM_POLISH=_bool_value(
        os.getenv(
            "LANGUAGE_EXPRESSION_ANALYSIS_ENABLE_FINAL_LLM_POLISH",
            _cfg.get("LANGUAGE_EXPRESSION_ANALYSIS_ENABLE_FINAL_LLM_POLISH", True),
        ),
        True,
    ),
    COURSE_KNOWLEDGE_CORPUS_ANALYSIS_CHUNK_CHARS=_positive_int(
        os.getenv(
            "COURSE_KNOWLEDGE_CORPUS_ANALYSIS_CHUNK_CHARS",
            _cfg.get("COURSE_KNOWLEDGE_CORPUS_ANALYSIS_CHUNK_CHARS", 500),
        ),
        500,
    ),
    COURSE_KNOWLEDGE_CORPUS_ANALYSIS_CHUNK_OVERLAP_CHARS=_non_negative_int(
        os.getenv(
            "COURSE_KNOWLEDGE_CORPUS_ANALYSIS_CHUNK_OVERLAP_CHARS",
            _cfg.get("COURSE_KNOWLEDGE_CORPUS_ANALYSIS_CHUNK_OVERLAP_CHARS", 60),
        ),
        60,
    ),
    COURSE_KNOWLEDGE_CORPUS_ANALYSIS_MAX_CHUNKS=_positive_int(
        os.getenv(
            "COURSE_KNOWLEDGE_CORPUS_ANALYSIS_MAX_CHUNKS",
            _cfg.get("COURSE_KNOWLEDGE_CORPUS_ANALYSIS_MAX_CHUNKS", 40),
        ),
        40,
    ),
    COURSE_KNOWLEDGE_CORPUS_ANALYSIS_CHUNK_CONCURRENCY=_positive_int(
        os.getenv(
            "COURSE_KNOWLEDGE_CORPUS_ANALYSIS_CHUNK_CONCURRENCY",
            _cfg.get("COURSE_KNOWLEDGE_CORPUS_ANALYSIS_CHUNK_CONCURRENCY", 4),
        ),
        4,
    ),
    COURSE_KNOWLEDGE_CORPUS_ANALYSIS_CHUNK_RETRY_ATTEMPTS=_positive_int(
        os.getenv(
            "COURSE_KNOWLEDGE_CORPUS_ANALYSIS_CHUNK_RETRY_ATTEMPTS",
            _cfg.get("COURSE_KNOWLEDGE_CORPUS_ANALYSIS_CHUNK_RETRY_ATTEMPTS", 2),
        ),
        2,
    ),
    COURSE_KNOWLEDGE_CORPUS_ANALYSIS_FINAL_RETRY_ATTEMPTS=_positive_int(
        os.getenv(
            "COURSE_KNOWLEDGE_CORPUS_ANALYSIS_FINAL_RETRY_ATTEMPTS",
            _cfg.get("COURSE_KNOWLEDGE_CORPUS_ANALYSIS_FINAL_RETRY_ATTEMPTS", 2),
        ),
        2,
    ),
    COURSE_KNOWLEDGE_CORPUS_ANALYSIS_CHUNK_MAX_KNOWLEDGE_POINTS=_positive_int(
        os.getenv(
            "COURSE_KNOWLEDGE_CORPUS_ANALYSIS_CHUNK_MAX_KNOWLEDGE_POINTS",
            _cfg.get("COURSE_KNOWLEDGE_CORPUS_ANALYSIS_CHUNK_MAX_KNOWLEDGE_POINTS", 3),
        ),
        3,
    ),
    COURSE_KNOWLEDGE_CORPUS_ANALYSIS_CHUNK_MAX_CORPUS=_positive_int(
        os.getenv(
            "COURSE_KNOWLEDGE_CORPUS_ANALYSIS_CHUNK_MAX_CORPUS",
            _cfg.get("COURSE_KNOWLEDGE_CORPUS_ANALYSIS_CHUNK_MAX_CORPUS", 3),
        ),
        3,
    ),
    COURSE_KNOWLEDGE_CORPUS_ANALYSIS_FINAL_MAX_KNOWLEDGE_POINTS=_positive_int(
        os.getenv(
            "COURSE_KNOWLEDGE_CORPUS_ANALYSIS_FINAL_MAX_KNOWLEDGE_POINTS",
            _cfg.get("COURSE_KNOWLEDGE_CORPUS_ANALYSIS_FINAL_MAX_KNOWLEDGE_POINTS", 20),
        ),
        20,
    ),
    COURSE_KNOWLEDGE_CORPUS_ANALYSIS_FINAL_MAX_CORPUS=_positive_int(
        os.getenv(
            "COURSE_KNOWLEDGE_CORPUS_ANALYSIS_FINAL_MAX_CORPUS",
            _cfg.get("COURSE_KNOWLEDGE_CORPUS_ANALYSIS_FINAL_MAX_CORPUS", 20),
        ),
        20,
    ),
    COURSE_KNOWLEDGE_CORPUS_ANALYSIS_MAX_DESCRIPTION_CHARS=_positive_int(
        os.getenv(
            "COURSE_KNOWLEDGE_CORPUS_ANALYSIS_MAX_DESCRIPTION_CHARS",
            _cfg.get("COURSE_KNOWLEDGE_CORPUS_ANALYSIS_MAX_DESCRIPTION_CHARS", 120),
        ),
        120,
    ),
    COURSE_KNOWLEDGE_CORPUS_ANALYSIS_MAX_CORPUS_CONTENT_CHARS=_positive_int(
        os.getenv(
            "COURSE_KNOWLEDGE_CORPUS_ANALYSIS_MAX_CORPUS_CONTENT_CHARS",
            _cfg.get("COURSE_KNOWLEDGE_CORPUS_ANALYSIS_MAX_CORPUS_CONTENT_CHARS", 240),
        ),
        240,
    ),
    STUDENT_INTERACTION_ANALYSIS_CHUNK_CHARS=_positive_int(
        os.getenv(
            "STUDENT_INTERACTION_ANALYSIS_CHUNK_CHARS",
            _cfg.get("STUDENT_INTERACTION_ANALYSIS_CHUNK_CHARS", 2000),
        ),
        2000,
    ),
    STUDENT_INTERACTION_ANALYSIS_CHUNK_OVERLAP_CHARS=_non_negative_int(
        os.getenv(
            "STUDENT_INTERACTION_ANALYSIS_CHUNK_OVERLAP_CHARS",
            _cfg.get("STUDENT_INTERACTION_ANALYSIS_CHUNK_OVERLAP_CHARS", 150),
        ),
        150,
    ),
    STUDENT_INTERACTION_ANALYSIS_CHUNK_CONCURRENCY=_positive_int(
        os.getenv(
            "STUDENT_INTERACTION_ANALYSIS_CHUNK_CONCURRENCY",
            _cfg.get("STUDENT_INTERACTION_ANALYSIS_CHUNK_CONCURRENCY", 4),
        ),
        4,
    ),
    STUDENT_INTERACTION_ANALYSIS_CHUNK_RETRY_ATTEMPTS=_positive_int(
        os.getenv(
            "STUDENT_INTERACTION_ANALYSIS_CHUNK_RETRY_ATTEMPTS",
            _cfg.get("STUDENT_INTERACTION_ANALYSIS_CHUNK_RETRY_ATTEMPTS", 2),
        ),
        2,
    ),
    STUDENT_INTERACTION_ANALYSIS_VERIFY_CONTEXT_SECONDS=_positive_int(
        os.getenv(
            "STUDENT_INTERACTION_ANALYSIS_VERIFY_CONTEXT_SECONDS",
            _cfg.get("STUDENT_INTERACTION_ANALYSIS_VERIFY_CONTEXT_SECONDS", 30),
        ),
        30,
    ),
    STUDENT_INTERACTION_ANALYSIS_VERIFY_RETRY_ATTEMPTS=_positive_int(
        os.getenv(
            "STUDENT_INTERACTION_ANALYSIS_VERIFY_RETRY_ATTEMPTS",
            _cfg.get("STUDENT_INTERACTION_ANALYSIS_VERIFY_RETRY_ATTEMPTS", 2),
        ),
        2,
    ),
    STUDENT_INTERACTION_ANALYSIS_MERGE_GAP_SECONDS=_positive_int(
        os.getenv(
            "STUDENT_INTERACTION_ANALYSIS_MERGE_GAP_SECONDS",
            _cfg.get("STUDENT_INTERACTION_ANALYSIS_MERGE_GAP_SECONDS", 30),
        ),
        30,
    ),
    STUDENT_INTERACTION_ANALYSIS_MAX_CHUNKS=_positive_int(
        os.getenv(
            "STUDENT_INTERACTION_ANALYSIS_MAX_CHUNKS",
            _cfg.get("STUDENT_INTERACTION_ANALYSIS_MAX_CHUNKS", 80),
        ),
        80,
    ),
    STUDENT_INTERACTION_ANALYSIS_MAX_CANDIDATES_PER_CHUNK=_positive_int(
        os.getenv(
            "STUDENT_INTERACTION_ANALYSIS_MAX_CANDIDATES_PER_CHUNK",
            _cfg.get("STUDENT_INTERACTION_ANALYSIS_MAX_CANDIDATES_PER_CHUNK", 3),
        ),
        3,
    ),
    QUESTION_CLASSIFICATION_LLM_CONCURRENCY=_positive_int(
        os.getenv(
            "QUESTION_CLASSIFICATION_LLM_CONCURRENCY",
            _cfg.get("QUESTION_CLASSIFICATION_LLM_CONCURRENCY", 4),
        ),
        4,
    ),
)
