# ADR 0002 — An internal network with no egress for the storage tier

- **Status:** accepted
- **Date:** 2026-11-07
- **Affects:** `docker/docker-compose.yaml`, `docker/docker-compose.dev.yaml`

## Context

Twelve containers need to talk to each other and to the outside world in different ways:

| Service | Profile | Needs inbound from | Needs outbound to | Network(s) |
|---|---|---|---|---|
| `gateway` | always | host | `backend`, `frontend` | `default`, `internal` |
| `frontend` | always | `gateway` | nothing | `default` |
| `backend` | always | `gateway` | `mysql`, `ml` — and nothing else; it holds no model code | `default`, `internal` |
| `mysql` | always | `backend` | nothing | `internal` |
| `cache` | `cache` | `backend`, the celery workers | nothing | `internal` |
| `storage` | `storage` | `backend`, the celery workers | nothing | `internal` |
| `ml` | `prod`, `dev` | `backend`, the celery workers | the internet: Hugging Face weights on first use, the only downloads in the stack | `default`, `internal` |
| `celery-worker-agents`, `celery-worker-maintenance`, `celery-worker-ml`, `celery-beat` | `celery` | nothing (they pull from Redis) | nothing | `internal` |
| `mailpit` | `mail` | `backend`, the `agents` worker | nothing | `internal` |

## Decision

**Two networks with different egress behavior:**

- `internal` — `bridge` + `internal: true`, therefore **no route to the internet**
  (`docker/docker-compose.yaml:824-826`). Members: `mysql`, `backend`, `gateway`,
  `cache`, `storage`, `ml`, the three celery workers, `celery-beat` and `mailpit`.
- `default` — a normal bridge with NAT egress (`:823-824`). Members: `gateway`,
  `frontend`, `backend`, `ml`.

Three services are dual-homed. `backend` is reached by the gateway and calls `ml`, and it
no longer needs egress of its own because the model downloads left the tree with the
pipeline; `gateway` needs the host publication that only `default` carries and must
resolve `backend`, `frontend` and `ml` by name; `ml` is reached by `backend` and must be
able to download a model on a host whose cache is empty. `frontend` is the only service
on `default` alone — it serves static files and reaches nothing. Every optional service
sits on `internal` only, so a profile that is switched on adds no new egress path (see the
table above for the per-service membership).

## Rationale

1. **The database has no reason to speak to the internet, and every reason not to.** A
   MySQL container that can open an outbound connection is an exfiltration path for
   exactly the data that matters most — candidate records, uploaded videos, embeddings.
   Removing the route removes the capability rather than relying on it not being used.
   An `ATTACH DATABASE`-style trick, a malicious `LOAD DATA` URL, or a compromised
   dependency in the container all stop at the network boundary.
2. **Failure modes get shorter.** With no egress, an outbound connection from the storage
   tier fails immediately (no route) instead of hanging until a timeout, so a
   misconfiguration surfaces as a fast error in the logs rather than a 30-second stall
   inside a request.
3. **Two networks are cheap.** Docker networks are virtual bridges; the cost is a second
   IP per attached container. Only `backend`, `gateway` and `ml` pay it, and each has to
   be dual-homed anyway: the backend because it answers the gateway *and* calls the
   sidecar, the gateway because it carries the host publication *and* is the only route
   to `backend`, `frontend` and `ml`, the sidecar because it is reached by the API *and*
   fetches a model on a host whose cache is still empty.
4. **It documents the architecture.** Reading the compose file tells you which tier a
   service belongs to. A service missing from `internal` is a bug that is visible by
   inspection, not a runtime discovery.

The sidecar, not the backend, is the service that keeps egress: it downloads
`facebook/wav2vec2-large-960h-lv60-self` and `dima806/facial_emotions_image_detection`
on first use. The backend is dual-homed only because it must answer the gateway and call
`ml`. In an air-gapped deployment the model cache is pre-seeded (`hf_cache` is a named
volume, and the images can be shipped with it filled) and `ml` could move fully onto
`internal` — a deliberate future option, not the default, because the default must work
on a machine that has internet.

## Alternatives rejected

- **One network for everything.** Simplest to write, and it gives MySQL egress for no
  benefit. Rejected on point 1.
- **Three networks (edge / app / storage).** More precise on paper, but `backend` and
  `gateway` would still each sit on two, and the edge/app split buys nothing for two
  stateless HTTP servers with no secrets (`frontend` reaches nothing; `gateway` only
  routes). Two networks keep the mental model small enough to check by reading a
  compose file.
- **No network at all, host networking.** Would expose MySQL on the host and defeat the
  point of container isolation.

## Consequences

- Any new service must be assigned to a network explicitly. A service on `default` by
  default (compose's implicit behavior) is a silent security regression, so every service
  in both compose files declares its networks.

  A service that needs no outgoing traffic but is missing from `internal` is a bug: the
  boundary was drawn in the wrong place.

- `ml` is the one service whose dual attachment is load-bearing rather than incidental.
  It exposes unauthenticated endpoints (`/v1/embed/video` costs a GPU minute), so it
  publishes no port on either network: being on `default` lets it resolve and be resolved
  inside the compose project, not from the host (`docker/docker-compose.yaml:562-563`).
  The compose files state membership per service rather than relying on a default.
- Because `ml` downloads its own weights, the two host directories are mounted into it
  and nowhere else: the torch checkpoints from `infra/models/`
  (`docker/docker-compose.yaml:520-521`) and the Hugging Face cache through the `hf_cache`
  named volume, which only `ml` mounts (`:525`). Before the split the API mounted the same
  two directories and also held the cache, because it could run the pipeline itself. With
  the in-process path gone the API opens no model file, so the mount moved with the code
  (`docker/docker-compose.yaml:132-137`).

- `docker compose exec` into `mysql` still works — the internal network restricts
  egress, not the Docker control plane.
- Health checks that use `curl` from inside `mysql` would fail; they use
  `mysqladmin ping` instead.
