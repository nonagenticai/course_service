# Operation ID Plan: course_service

Canonical registry of `operation_id` values for every route in
`course_service`. These IDs feed the org-wide n8n custom-node generator
and the `ultrareview` cross-service catalogue, so they must be:

- **stable** — never rename without coordinating downstream,
- **`verb_noun` snake_case** — matches the org standard (nlp_service,
  worker_management, kag, analysis_service, video_compiler_service,
  video_analyzer_service),
- **globally unique** — disambiguated against the rest of the fleet's verbs.

This service hosts the long-form course lifecycle plus a health surface.
Verbs that are generic at fleet scope (`callback`, `bundle`, `manifest`,
`progress`) are domain-prefixed with `course_` to avoid collision.

## Files to edit

- `src/asgi.py` (root endpoint)
- `src/api/health.py`
- `src/api/routes.py`

## Mapping Tables

### `src/asgi.py` (tag: `health`)

| Method | Function | operation_id   | Rationale                                  |
|--------|----------|----------------|--------------------------------------------|
| GET    | `root`   | `service_root` | Service banner; matches the fleet standard. |

### `src/api/health.py` (tag: `health`)

| Method | Function    | operation_id   | Rationale                                                  |
|--------|-------------|----------------|------------------------------------------------------------|
| GET    | `health`    | `health_check` | Primary health probe; matches the fleet standard verb.     |
| GET    | `liveness`  | `health_live`  | Liveness sub-probe; one-to-one with path.                  |
| GET    | `readiness` | `health_ready` | Readiness sub-probe; one-to-one with path.                 |

### `src/api/routes.py` (tag: `courses`)

| Method | Function         | operation_id                     | Rationale                                                                                                  |
|--------|------------------|----------------------------------|------------------------------------------------------------------------------------------------------------|
| POST   | `create_course`  | `create_course`                  | Domain noun already in the function name; no collision risk at fleet scope.                                |
| GET    | `list_courses`   | `list_courses`                   | Same — domain noun is course-specific.                                                                     |
| GET    | `get_course`     | `get_course`                     | Same.                                                                                                      |
| DELETE | `delete_course`  | `delete_course`                  | Same.                                                                                                      |
| GET    | `get_progress`   | `get_course_progress`            | Generic `progress` would collide (analysis/orchestrator/video have their own progress views); prefix.       |
| POST   | `callback`       | `receive_course_lesson_callback` | Generic `callback` is fleet-wide; prefix with domain + payload-shape (`lesson` = the orchestrator job that's reporting). |
| GET    | `get_bundle`     | `get_course_bundle`              | Generic `get_bundle` collides with `analysis_service`-style bundle artifacts; prefix.                       |
| GET    | `get_manifest`   | `get_course_manifest`            | Generic `get_manifest` collides with video/bundle manifests in other services; prefix.                      |

## Verification

```bash
cd course_service
uv run pytest tests/unit/test_openapi.py -x
```

`tests/unit/test_openapi.py` loads `app.openapi()` and asserts that the
set of `operationId` values equals the union of every right-hand column
above. Drift (rename, missed decorator, fallback) fails the test loudly.

## Disambiguation rationale

Concrete renames where the function name was generic enough to collide
across the fleet:

- `get_progress` → `get_course_progress` — `progress` is a shared verb
  across analysis_service, orchestrator (lesson-pipeline progress),
  and video_analyzer_service.
- `callback` → `receive_course_lesson_callback` — `callback` alone is
  meaningless in a generated-client catalogue; the descriptive form
  encodes both the domain (`course`) and what is being reported
  (`lesson` pipeline completion from orchestrator).
- `get_bundle` → `get_course_bundle` — `bundle` is a generic artifact
  noun used in `analysis_service`/`video_compiler_service`; prefix.
- `get_manifest` → `get_course_manifest` — `manifest` is shared with
  video/bundle manifests; prefix.

The four routes whose function names are already domain-prefixed
(`create_course`, `list_courses`, `get_course`, `delete_course`) keep
their function-name shape as the operation_id — no rewrite needed.

## Future routes

When adding a route in this service, **always** set `operation_id="…"`
explicitly. The `generate_unique_id_function` configured in `asgi.py` is a
safety net for forgotten kwargs, never the primary identifier. New IDs go
in the table above as part of the same PR (no out-of-band updates).
