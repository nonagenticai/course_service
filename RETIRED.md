# ⛔ RETIRED — 2026-06-13

`course_service` is **retired**. It is no longer deployed and no longer runs CI.

## Why

- **Orphaned**: nothing in the fleet consumed it — no orchestrator `CourseClient`,
  no frontend HTTP call, no public istio/cloudflared route, no n8n node.
- **Dead integration**: its `OrchestratorClient` targeted `/api/pipeline`, an endpoint
  the orchestrator **deleted in the v3 (Studio) reset**. The service was broken
  regardless of auth; recent commits were chore-only (lock/CVE bumps, ruff, alembic
  drift alignment).
- **Superseded**: curriculum planning, lesson dispatch, and course assembly are now
  owned by **w2v_orchestrator blueprint stages** —
  `src/services/stages/course/{course_outline,compile_lessons,stitch,thumbnail}.py`
  and `src/services/stages/lesson/{script,narrate,compile}.py`, driven by `WMClient`
  + `VideoCompileClient`.

## What was removed

- This repo's CI (`.github/workflows/ci-cd.yaml`) — no more image build/promote.
- All deployment manifests and flux image automation — the follow-up change
  removing the `course-service` Deployment/Service/ConfigMap/Secret, the
  `w2v-course` ImageRepository/ImagePolicy, and the pgbouncer / postgres-ci routes.

## Still outstanding (manual)

- `DROP DATABASE course_service` (postgres :5432) + `course_service_ci` (:5433) and
  the DB user, to reclaim the storage. Left as an explicit human step.
- The `w2v_common.discovery` `"course"` catalog entry is left in place (removing it
  would force a fleet relock); it is a harmless dangling entry.

## Do NOT revive without

Re-wiring the orchestrator integration against the **v3 runs API**
(`POST /api/deliverables/{id}/runs`, the projects/deliverables/runs surface) — and
first establishing a distinct product role, since the orchestrator already owns
course generation.
