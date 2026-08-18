import numpy as np
import pytest


def test_filter_candidate_embeddings_records_missing_and_wrong_dimensions() -> None:
    from app.core.embedding_matching import filter_candidate_embeddings

    valid_vector = np.ones(512, dtype=np.float32)
    vectors, documents, rejections = filter_candidate_embeddings(
        [
            {"person_id": "missing"},
            {"person_id": "wrong-dimension", "embedding": b"\x00\x00\x00\x00"},
            {"person_id": "valid", "embedding": valid_vector.tobytes()},
        ]
    )

    assert len(vectors) == 1
    assert vectors[0].shape == (512,)
    np.testing.assert_allclose(
        vectors[0],
        valid_vector / np.linalg.norm(valid_vector),
    )
    assert [document["person_id"] for document in documents] == ["valid"]
    assert rejections == [
        {"record": "missing", "reason": "embedding_missing"},
        {"record": "wrong-dimension", "reason": "embedding_dimension_invalid"},
    ]


@pytest.mark.parametrize(
    ("record", "candidate"),
    [
        (
            "nan",
            np.concatenate(
                ([np.nan], np.ones(511, dtype=np.float32))
            ).astype(np.float32),
        ),
        (
            "infinity",
            np.concatenate(
                ([np.inf], np.ones(511, dtype=np.float32))
            ).astype(np.float32),
        ),
        ("zero-norm", np.zeros(512, dtype=np.float32)),
        (
            "non-finite-norm",
            np.full(512, np.finfo(np.float32).max, dtype=np.float32),
        ),
    ],
)
def test_filter_candidate_embeddings_rejects_invalid_values_and_norms(
    record: str,
    candidate: np.ndarray,
) -> None:
    from app.core.embedding_matching import filter_candidate_embeddings

    vectors, documents, rejections = filter_candidate_embeddings(
        [{"person_id": record, "embedding": candidate.tobytes()}]
    )

    assert vectors == []
    assert documents == []
    assert rejections == [
        {"record": record, "reason": "embedding_values_invalid"}
    ]


def test_find_best_match_ignores_non_finite_candidate() -> None:
    from app.core.ai_engine import find_best_match_embedding

    query = np.zeros(512, dtype=np.float32)
    query[0] = 1.0
    valid = query.copy()
    invalid = np.ones(512, dtype=np.float32)
    invalid[0] = np.nan

    similarity, document = find_best_match_embedding(
        query,
        [
            {"person_id": "invalid", "embedding": invalid.tobytes()},
            {"person_id": "valid", "embedding": valid.tobytes()},
        ],
    )

    assert similarity == pytest.approx(1.0)
    assert document is not None
    assert document["person_id"] == "valid"


def test_find_top_matches_excludes_invalid_candidates_from_ranking() -> None:
    from app.core.ai_engine import find_top_matches

    query = np.zeros(512, dtype=np.float32)
    query[0] = 1.0
    best = query.copy()
    second = np.zeros(512, dtype=np.float32)
    second[:2] = [0.6, 0.8]
    non_finite = np.ones(512, dtype=np.float32)
    non_finite[0] = np.inf

    matches = find_top_matches(
        query,
        [
            {"person_id": "zero", "embedding": np.zeros(512, dtype=np.float32).tobytes()},
            {"person_id": "non-finite", "embedding": non_finite.tobytes()},
            {"person_id": "best", "embedding": best.tobytes()},
            {"person_id": "second", "embedding": second.tobytes()},
        ],
        top_k=4,
        min_threshold=0.0,
    )

    assert [document["person_id"] for _, document in matches] == ["best", "second"]
    assert [similarity for similarity, _ in matches] == pytest.approx([1.0, 0.6])
