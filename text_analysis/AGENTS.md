# Text Analysis Operator Guide

## Scope

This service performs education-domain text analysis through an OpenAI-compatible LLM endpoint. The scheduling platform currently uses `/v1/course_overviews` for course mind maps and `/v1/extract_keywords` for PPT-image text keywords.

The project still exposes additional historical text-analysis APIs. Do not remove, rename or change those routes as part of project-layout or deployment work; interface reduction requires a separately approved change.

## Runtime

- Entry point: `app.main:app`
- Local Conda environment: `ai_report`
- Default port: `8000`
- Configuration: project-root `config.toml`
- Configuration override: `CONFIG_PATH=/absolute/path/to/config.toml`
- Prompt assets: project-root `prompt/`

Run from the `text_analysis/` project root:

```bash
conda run -n ai_report python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

Docker and `start.sh` must continue to use `app.main:app`. Resolve configuration and prompt paths from the project root; do not depend on the old `llm_api_refactor` directory name.

## Compatibility Boundaries

- Preserve all existing HTTP paths, methods, request fields and response fields unless the user explicitly approves a contract change.
- Do not change Prompt content, LLM request parameters, retry behavior, timeout behavior or response normalization during structural work.
- `/v1/course_overviews` and `/v1/extract_keywords` are the two endpoints registered by the scheduling platform in the current architecture; this does not authorize deleting other service routes.
- Keep the default port at `8000` and the FastAPI version at `1.0.0`.
- Never commit API keys or replace deployment endpoint values without explicit direction.

## Verification

Run after structural or deployment changes:

```bash
conda run -n ai_report python -m compileall -q app
conda run -n ai_report python -m unittest discover -s tests -v
conda run -n ai_report python -m pip check
```

Also import `app.main:app`, compare the complete route list with the pre-change baseline, and verify Dockerfile plus `start.sh` still reference `app.main:app`.
