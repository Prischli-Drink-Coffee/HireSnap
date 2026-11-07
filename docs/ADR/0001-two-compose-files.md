# ADR 0001 — Two compose files instead of one file with overlays

- **Status:** accepted
- **Date:** 2026-11-07
- **Affects:** `docker/docker-compose.yaml`, `docker/docker-compose.dev.yaml`, `docker/run.sh`

## Context

The stack has exactly two deployment shapes:

| | production | development |
|---|---|---|
| Project name | `hiresnap-prod` | `hiresnap-dev` |
| Published ports | gateway only | gateway, backend, frontend |
| Backend `DEBUG` | `FALSE` | `TRUE` |
| Frontend | static bundle in nginx | CRA dev server with hot reload |
| Source mounts | none | `backend/src`, `frontend/src` |
| Secrets | every secret uses `${VAR:?...}` | placeholders tolerated |

Docker Compose supports two ways to express this: one base file plus override files
(`-f compose.yaml -f compose.dev.yaml`), or two complete, independent files.

## Decision

**Two complete files.** `docker/docker-compose.yaml` is production;
`docker/docker-compose.dev.yaml` is development. Each one is valid on its own — there
is no base file to layer onto, and no `extends:`.

## Rationale

1. **A dev override cannot remove a production port publication.** Override files merge;
   they do not subtract. To keep the backend unpublished in production and published in
   dev, the port must be declared in exactly one of them. With two full files that is
   trivial; with a base-plus-override pair, the base would have to leave the port out and
   the dev file would have to re-declare the whole service — which is the same as
   writing two files, only harder to read.
2. **The services are genuinely different, not tweaked.** In dev the frontend is a CRA
   dev server (`npm start`) with source mounted; in prod it is a static nginx image
   serving `build/`. That is a different image build target and a different command,
   not a setting. Expressing it as an override produces a file whose diff against the
   base is larger than the service definition.
3. **Readability at the point of danger.** Someone editing production deploy code opens
   `docker-compose.yaml` and sees *the whole production stack*, including the fact that
   only one port is published. With overlays, the answer to "what does prod actually
   run?" is spread across two files and must be computed with `docker compose config`,
   which people do not run before editing.
4. **Compose project isolation is explicit.** Each file declares its own `name:`
   (`hiresnap-prod` at `docker-compose.yaml:24`, `hiresnap-dev` at
   `docker-compose.dev.yaml:23`), and `docker/run.sh` passes `-p $PROJECT_NAME` on every
   invocation, where `PROJECT_NAME` comes from the env file itself. Two files make the
   project name visible in one line at the top of each; an overridden project name would
   have to be wrong in two places before containers, networks or volumes could collide
   with the other environment.

Cost accepted: duplicated service definitions. When a service changes — say the backend
gains an environment key — it must be edited in both files. This is a real cost, and it
is the reason two guards exist. `scripts/check_env_parity.py` fails when a compose
file interpolates a key the manifest does not declare, or when the three env files
disagree; `scripts/check_compose_parity.py` fails when the two files drift in ways that
matter — an unpinned image, a published port in prod, a missing internal network. A key
added to one file and forgotten in the other therefore fails `make lint` rather than at
02:00.

## Alternatives rejected

- **One file, both environments via profiles.** Profiles select services, not port
  publications or commands. They cannot express "publish this port in dev only".
- **Base plus override.** See rationale 1 and 2: Compose merges but does not subtract,
  and the frontend differs by more than any override should.
- **Two files generated from a template.** A generator is a build step, and it makes the
  generated files the thing nobody reads. Two hand-written files plus a guard that
  checks them is simpler than a generator plus a guard.

## Consequences

- Both files must be kept in sync for the services they share. The env-parity guard
  catches key drift; a reviewer catches everything else.
- `docker/run.sh` picks the file from its first argument (`--dev` / `--prod`) and passes
  `--env-file` and `-p $PROJECT_NAME` explicitly, so a stray `COMPOSE_PROJECT_NAME` in the
  shell cannot silently redirect a deploy.
- Operators must know which file they are looking at. Both files carry a header comment
  saying so, and the project names differ (`hiresnap-prod` / `hiresnap-dev`).
