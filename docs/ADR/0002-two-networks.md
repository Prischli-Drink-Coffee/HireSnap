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
| `backend` | always | `gateway` | `mysql`, `ml`, and the internet (Hugging Face model downloads on first use) | `default`, `internal` |
| `mysql` | always | `backend` | nothing | `internal` |
| `cache` | `cache` | `backend`, the celery workers | nothing | `internal` |
| `storage` | `storage` | `backend`, the celery workers | nothing | `internal` |
| `ml` | `ml` | `backend` | nothing (weights are mounted, not fetched) | `internal` |
| `celery-worker-agents`, `celery-worker-maintenance`, `celery-worker-ml`, `celery-beat` | `celery` | nothing (they pull from Redis) | nothing | `internal` |
| `mailpit` | `mail` | `backend`, the `agents` worker | nothing | `internal` |

## Decision

**Two networks with different egress behavior:**

- `internal` — `bridge` + `internal: true`, therefore **no route to the internet**
  (`docker/docker-compose.yaml:703-705`). Members: `mysql`, `backend`, `gateway`,
  `cache`, `storage`, `ml`, the three celery workers, `celery-beat` and `mailpit`.
- `default` — a normal bridge with NAT egress (`:698-699`). Members: `gateway`,
  `frontend`, `backend`.

Two services are dual-homed. `backend` needs egress (Hugging Face downloads on first
use) and is reached by the gateway; `gateway` needs the host publication that only
`default` carries and must resolve `backend`, `frontend` and `ml` by name. `frontend`
is the only service on `default` alone — it serves static files and reaches nothing.
Every optional service sits on `internal` only, so a profile that is switched on adds
no new egress path (see the table above for the per-service membership).

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
   IP per attached container. Only `backend` and `gateway` pay it, and both have to be
   dual-homed anyway: the backend because it needs egress *and* is reached by a sibling,
   the gateway because it carries the host publication *and* is the only route to
   `backend`, `frontend` and `ml`.
4. **It documents the architecture.** Reading the compose file tells you which tier a
   service belongs to. A service missing from `internal` is a bug that is visible by
   inspection, not a runtime discovery.

The backend keeps egress because it downloads model weights on first use
(`facebook/wav2vec2-large-960h-lv60-self`,
`dima806/facial_emotions_image_detection`) and because it must be reachable from the
gateway. In an air-gapped deployment the model cache is pre-seeded at image build time
and the backend could move fully onto `internal` — that is a deliberate future option,
not the default, because the default must work on a machine that has internet.

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

- A profile service lands on `internal` only, deliberately. `ml` exposes
  unauthenticated endpoints (`/v1/embed/video` costs a GPU minute), so keeping it off
  `default` means it is never reachable from a network that has a published port
  (`docker/docker-compose.yaml:467-472`). The compose files state this per service rather
  than relying on a default.
- The trade-off is that `ml` cannot fetch anything at request time, so both kinds of
  model have to arrive another way: the torch checkpoints are bind-mounted from
  `infra/models/` (`docker/docker-compose.yaml:435-436`), and the Hugging Face models
  arrive through the `hf_cache` named volume, which `backend` and `ml` share while only
  `backend` has egress (`:138` and `:443`). That is the concrete reason the cache is a
  shared volume rather than a per-container directory: it is the one channel by which a
  no-egress sidecar can be given a model that was downloaded once.

- `docker compose exec` into `mysql` still works — the internal network restricts
  egress, not the Docker control plane.
- Health checks that use `curl` from inside `mysql` would fail; they use
  `mysqladmin ping` instead.
