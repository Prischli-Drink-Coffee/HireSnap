# Architecture decision records

Short records of the decisions that are expensive to reverse. Each one states the
context, the decision, the rationale, the alternatives that were rejected, and the
consequences — including the costs we accepted.

| ADR | Decision | Read it when |
|-----|----------|--------------|
| [0001](0001-two-compose-files.md) | Two complete compose files instead of a base plus overrides | You are tempted to merge them, or you are editing one and wondering why the other exists |
| [0002](0002-two-networks.md) | An `internal` network with no egress for the storage tier | You are adding a service, or asking why MySQL cannot reach the internet |
| [0003](0003-nginx-rewrites.md) | Rewrite `/api/` to `/server/` at the gateway rather than editing either app | You wonder why every API call goes through a prefix swap |
| [0004](0004-double-underscore-env.md) | `DB__HOST`-style keys in env files, flat names in containers | You add a variable and are unsure which name goes where |
| [0005](0005-source-patches.md) | Five enumerated source-level changes, and the list is closed | You are about to edit application source, or you are re-applying the patches after a reset |
| [0006](0006-ml-sidecar-owns-inference.md) | The sidecar owns the inference code; the backend keeps an HTTP client | You are about to put a GPU framework back in `backend/`, or you are wiring a new inference route |
| [0007](0007-sidecar-package-layout.md) | One importable package per sidecar: `service/` on the backend, `app/`+`pages/`+`shared/` on the frontend | You are about to add a top-level directory, or you are looking for a module that moved out of `src/` |

## Conventions

- **Numbered and immutable.** Once accepted, an ADR is not edited to reflect a new
  decision; a superseding ADR is written and the old one is marked `Superseded by NNNN`.
  The history of *why* is the point — rewriting it destroys the record.
- **One decision per file.** "Use two compose files and also change the networks" is two
  ADRs, because they can be reversed independently.
- **Consequences are mandatory and include costs.** An ADR that lists only benefits is a
  sales pitch. Every file here names at least one thing that is now harder.
- **Dates are the date of the decision, not of the last edit.**

## Not covered here

Things that are conventions rather than decisions — commit rules, where generated state
lives, code style — belong in [AGENTS.md](../../AGENTS.md). Things that are defects and
not decisions belong in [KNOWN_GAPS.md](../KNOWN_GAPS.md). An ADR explains a choice; it
never explains a bug.
