# Course Service

> ⛔ **RETIRED 2026-06-13** — not deployed, no build or deployment CI. Superseded
> by w2v_orchestrator v3 course/lesson blueprint stages. See [RETIRED.md](./RETIRED.md).

Course generation microservice (curriculum planning, lesson dispatch, course
assembly). **No longer in service.**

## Status: retired

This service is not deployed, runs no build or deployment CI, and has no gitops manifests. Its
`/api/pipeline` orchestrator integration was deleted in the v3 (Studio) reset, so
it does not function even if started. Course generation is now owned by
**w2v_orchestrator** blueprint stages — `src/services/stages/course/*` and
`src/services/stages/lesson/*`, driven by the projects→deliverables→runs API.

See **[RETIRED.md](./RETIRED.md)** for the full rationale, what was removed, and the
conditions under which it could ever be revived.

The source tree is preserved for history only. Do not start it, deploy it, or copy
its patterns — use a current sibling service as a template instead.

## Licensing

This is retired reference code and contains no licence enforcement of its own: it
calls no licence server and runs with no licence. Unset means off.

## License

Apache 2.0 — see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).
