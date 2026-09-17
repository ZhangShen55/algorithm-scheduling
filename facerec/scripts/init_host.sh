#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RUNTIME_ROOT="${FACEREC_RUNTIME_ROOT:-/data/facerec}"

required_models=(
  "ai_models/shape_predictor_68_face_landmarks.dat"
  "ai_models/ms1mv3_arcface_r100.onnx"
  "ai_models/models/buffalo_l/det_10g.onnx"
  "ai_models/models/buffalo_l/w600k_r50.onnx"
  "ai_models/models/buffalo_l/2d106det.onnx"
  "ai_models/models/buffalo_l/1k3d68.onnx"
  "ai_models/models/buffalo_l/genderage.onnx"
)

for relative_path in "${required_models[@]}"; do
  model_path="${PROJECT_ROOT}/${relative_path}"
  [[ -s "${model_path}" ]] || {
    echo "[ERROR] 模型缺失或为空: ${relative_path}" >&2
    exit 1
  }
done

install -d -m 0755 \
  "${RUNTIME_ROOT}/logs" \
  "${RUNTIME_ROOT}/media/person_photos" \
  "${RUNTIME_ROOT}/media/detections"

echo "[INFO] FaceRec 运行目录与七个模型校验完成: ${RUNTIME_ROOT}"
