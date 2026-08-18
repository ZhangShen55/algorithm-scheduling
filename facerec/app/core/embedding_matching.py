from typing import Any

import numpy as np
from bson.binary import Binary


def _record_identity(document: dict[str, Any]) -> str:
    for field in ("person_id", "number", "_id"):
        value = document.get(field)
        if value is not None:
            return str(value)
    return "<unknown>"


def filter_candidate_embeddings(
    candidate_docs: list[dict[str, Any]],
) -> tuple[list[np.ndarray], list[dict[str, Any]], list[dict[str, str]]]:
    vectors: list[np.ndarray] = []
    valid_docs: list[dict[str, Any]] = []
    rejections: list[dict[str, str]] = []
    for document in candidate_docs:
        embedding = document.get("embedding")
        identity = _record_identity(document)
        if embedding is None:
            rejections.append({"record": identity, "reason": "embedding_missing"})
            continue
        if isinstance(embedding, Binary):
            embedding = bytes(embedding)
        try:
            vector = np.frombuffer(embedding, dtype=np.float32)
        except (BufferError, TypeError, ValueError):
            rejections.append(
                {"record": identity, "reason": "embedding_dimension_invalid"}
            )
            continue
        if vector.size != 512:
            rejections.append(
                {"record": identity, "reason": "embedding_dimension_invalid"}
            )
            continue
        if not np.all(np.isfinite(vector)):
            rejections.append(
                {"record": identity, "reason": "embedding_values_invalid"}
            )
            continue
        with np.errstate(invalid="ignore", over="ignore"):
            norm = np.linalg.norm(vector)
        if not np.isfinite(norm) or norm <= 0:
            rejections.append(
                {"record": identity, "reason": "embedding_values_invalid"}
            )
            continue
        vectors.append(vector / (norm + 1e-12))
        valid_docs.append(document)
    return vectors, valid_docs, rejections
