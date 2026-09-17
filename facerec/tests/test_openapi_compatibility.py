from app.main import app
from app.models.api_response import StatusCode


EXPECTED_PATH_METHODS = {
    "/": ["get"],
    "/ops/drain": ["post"],
    "/ops/health": ["get"],
    "/ops/metadata": ["get"],
    "/ops/metrics": ["get"],
    "/ops/stats/api-calls": ["get"],
    "/ops/stats/hourly": ["get"],
    "/ops/stats/summary": ["get"],
    "/ops/status": ["get"],
    "/persons": ["get", "post"],
    "/persons/batch": ["post"],
    "/persons/delete": ["delete"],
    "/persons/search": ["post"],
    "/recognize": ["post"],
    "/recognize/batch": ["post"],
}

EXPECTED_SCHEMA_FIELDS = {
    "APICallLogResponse": [
        "client_ip",
        "duration_ms",
        "error_message",
        "method",
        "path",
        "request_id",
        "status_code",
        "success",
        "timestamp",
    ],
    "APIStatsHourlyResponse": [
        "avg_response_time_ms",
        "date",
        "endpoint",
        "error_count",
        "hour",
        "max_response_time_ms",
        "method",
        "min_response_time_ms",
        "success_count",
        "success_rate",
        "total_requests",
    ],
    "APIStatsSummaryResponse": [
        "avg_response_time_ms",
        "hourly_distribution",
        "success_rate",
        "top_endpoints",
        "total_errors",
        "total_requests",
        "total_success",
    ],
    "ApiResponse": ["data", "message", "status_code"],
    "BatchRecognizeRequest": ["photos", "targets", "threshold"],
    "DeletePersonRequest": ["id", "name", "number"],
    "HTTPValidationError": ["detail"],
    "HealthCheckResponse": ["components", "status", "timestamp"],
    "OperatorLifecycle": [],
    "OperatorOpsMetadata": [
        "api_version",
        "capabilities",
        "instance_id",
        "model_version",
        "operator_code",
    ],
    "OperatorOpsStatus": [
        "declared_capacity",
        "inflight",
        "lifecycle",
        "model_ready",
    ],
    "PersonBatchFeatureRequest": ["name", "number", "photo"],
    "PersonFeatureRequest": ["name", "number", "photo"],
    "PersonRecognizeRequest": ["photo", "targets", "threshold"],
    "PersonsBatchFeatureRequest": ["persons"],
    "SearchPersonRequest": ["name", "number"],
    "SystemMetricsResponse": ["application", "system"],
    "ValidationError": ["loc", "msg", "type"],
}


def test_openapi_paths_methods_and_schema_fields_match_legacy_baseline() -> None:
    openapi = app.openapi()
    methods = {"get", "post", "put", "patch", "delete", "options", "head"}
    observed_paths = {
        path: sorted(method for method in definition if method in methods)
        for path, definition in openapi["paths"].items()
    }
    observed_schemas = {
        name: sorted(definition.get("properties", {}))
        for name, definition in openapi["components"]["schemas"].items()
    }

    assert observed_paths == EXPECTED_PATH_METHODS
    assert observed_schemas == EXPECTED_SCHEMA_FIELDS
    assert "points" not in observed_schemas["PersonRecognizeRequest"]
    assert "statusCode" not in observed_schemas["ApiResponse"]
    assert "hasFace" not in str(openapi)
    assert "bboxs" not in str(openapi)


def test_business_status_codes_match_legacy_values() -> None:
    assert {item.name: item.value for item in StatusCode} == {
        "SUCCESS": 200,
        "NO_FACE_DETECTED": 201,
        "FACE_TOO_SMALL": 202,
        "PARTIAL_SUCCESS": 207,
        "DB_EMPTY": 251,
        "NO_MATCH_FOUND": 252,
        "BAD_REQUEST": 400,
        "BASE64_DECODE_ERROR": 401,
        "INVALID_IMAGE_FORMAT": 402,
        "INVALID_IMAGE_DATA": 403,
        "NOT_FOUND": 404,
        "UNPROCESSABLE_ENTITY": 422,
        "INTERNAL_ERROR": 500,
        "FACE_DETECTION_ERROR": 501,
        "FEATURE_EXTRACT_ERROR": 502,
        "FILE_SAVE_ERROR": 503,
    }
