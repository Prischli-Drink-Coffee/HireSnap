# Secrets

Everything in this stack that must never be committed, how to generate it, where it
lives, and how to rotate it. Read this together with [ENV.md](ENV.md) (which variables
exist) and [RUNBOOK.md](RUNBOOK.md) (operational procedures).

## 1. Inventory

| Secret | Form | Where it lives | Committed? | Rotatable without data loss |
|--------|------|----------------|-----------|------------------------------|
| JWT private key | file `infra/certs/jwt-private.pem` | host, mounted into the backend at `/app/secrets/certs` | **never** | Yes — invalidates all issued tokens |
| JWT public key | file `infra/certs/jwt-public.pem` | same | **never** | Yes, together with the private key |
| Application DB password | env `DB__PASSWORD` / `DB_PASSWORD` | `docker/.env.dev`, `.env.prod` | **never** | Yes, with `ALTER USER` (see §4) |
| MySQL root password | env `MYSQL_ROOT_PASSWORD` | same | **never** | Yes, `ALTER USER 'root'@'%'` |
| MinIO credentials | env `STORAGE__ACCESS_KEY` / `STORAGE__SECRET_KEY` (container vars `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD`) | same | **never** | Yes |
| `cache` (Redis) | **none** — no password, internal network only | — | — | — |
| GitHub token / SSH key | host credentials | not a stack concern | **never** | — |

The single committed env file is `docker/.env.example`. It contains **key names and
placeholder values only** — no real password, no real key. Reference:
`R:\GPTHub\.gitignore:20-23` blocks the whole `.env` family and the reference
`.env.example` carries the same kind of non-secret placeholders.

## 2. Why the ignore rules are written the way they are

The superproject `.gitignore` ignores:

```gitignore
.env
.env.*
!.env.example
```

Three subtleties, each of which has bitten a real repository:

1. **`.env.*` also matches `.env.example`**, so the negation is mandatory. Drop the
   `!.env.example` line and the manifest silently stops being committed; a fresh clone
   then has no file to copy and the quickstart is broken for everyone but the person who
   wrote it.
2. **Ignore rules do not reach into submodules.** `R:\GPTHub\.gitignore:3-8` states
   this explicitly: the root repo tracks `backend` and `frontend` as gitlinks, so their
   files are filtered by *their own* `.gitignore` — `backend/.gitignore:181-182` still
   lists the retired `/src/weights/` and `/src/certs/` paths, because a rule can only
   protect what it names. A root rule protects root files only.
3. **Keys are files, not variables.** `infra/certs/` is ignored as a directory because
   the key material is written by `make env` and must never enter git; the directory
   itself may be committed with a `.gitkeep` if you want the mount point to exist.

## 3. JWT keys (RS256)

The backend signs and verifies tokens with an RSA key pair whose **path** is resolved when
the model is constructed, in one function (`backend/service/models/auth_models.py`,
`_key_path()`):

```python
def _key_path(variable: str, filename: str):
    raw = os.environ.get(variable)          # JWT_PRIVATE_KEY_PATH wins
    if raw and raw.strip():
        return Path(raw.strip())
    return CERTS_DIR / filename             # backend/service/certs on a bare checkout
```

The path is returned unresolved on purpose: a missing key must not be an import error, it
must be an error on the first route that signs a token. In the container the environment
variable decides — `.env` sets both to `/app/secrets/certs/*.pem` and the compose files
mount `infra/certs` there — while a bare checkout with no `.env` falls through to
`CERTS_DIR` (`backend/service/configuration/paths.py:40`).

The algorithm is fixed to `RS256` in the token layer. The filenames are part of the
contract: a key named `private.pem` is simply not found, and the failure appears as an
authentication error on every guarded route rather than as a startup error.

### 3.1 Generating a pair

```bash
mkdir -p infra/certs
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 \
  -out infra/certs/jwt-private.pem
openssl rsa -pubout -in infra/certs/jwt-private.pem \
  -out infra/certs/jwt-public.pem
chmod 600 infra/certs/jwt-private.pem
chmod 644 infra/certs/jwt-public.pem
```

`make env` does exactly this when the files are missing, and **only** then. 2048 bits is
the floor for RS256; 4096 works but roughly quadruples the signing cost per request, and
this backend signs on every login.

Why the pair is not generated at container start: two backend replicas generating keys
concurrently would produce two different pairs, and each would reject the other's
tokens. Key generation is a **deployment** step, executed once, by a human or by
`make env` — not a runtime step.

### 3.2 Mounting

| Host | Container | Mode |
|------|-----------|------|
| `infra/certs` | `/app/secrets/certs` | read-only |

Read-only is not decoration: the application never needs to write keys, and a read-only
mount turns a path bug into a loud `EROFS` instead of silent key corruption.

### 3.3 Rotating

Rotating invalidates **every token ever issued** — all users are logged out. Plan it:

1. Generate the new pair into a staging directory.
2. Stop the backend.
3. Replace both files atomically (move, do not edit in place — a half-written PEM is
   rejected by the crypto library with a confusing error).
4. Start the backend. The old `jwt-public.pem` is gone, so old tokens fail with
   `403 Invalid token or expired token.` — that is the intended outcome, not a bug.

There is no key-id (`kid`) support in the application, so two keys cannot be valid at
the same time. Any zero-downtime rotation would require an application change.

## 4. Database password

Two names hold the same value, and they are honored by **different mechanisms**:

| Key | Read by | When it takes effect |
|-----|---------|----------------------|
| `MYSQL_ROOT_PASSWORD`, `MYSQL_USER`, `MYSQL_PASSWORD` | the MySQL image's entrypoint | **only** on first boot, when the `mysql_data` named volume is empty |
| `DB__PASSWORD` → `DB_PASSWORD` | the application, via `backend/service/infrastructure/database/connection.py:58-65` | every time the backend connects |

That asymmetry is the most common operational mistake in this stack: editing
`MYSQL_PASSWORD` on a live volume does nothing, while `DB__PASSWORD` does change, and
the result is an authentication failure that looks like a code bug. The correct sequence
is in [RUNBOOK.md](RUNBOOK.md#11-rotating-the-database-password): change the password inside
MySQL first, then the env file, then recreate the backend.

Generate a password that survives a `.env` file without quoting drama:

```bash
# URL-safe alphabet: no $, no backtick, no quote, no backslash
openssl rand -base64 32 | tr -d '/+=' | cut -c1-32
```

Avoid `$`, backticks, quotes, and leading/trailing spaces. Compose interpolates `${...}` in
the file itself, so a literal dollar sign in a password will be interpreted — producing
a password the container never sees and a bug report nobody can reproduce locally.

## 5. Object storage credentials

Only relevant with `--profile storage`.

The manifest names these `STORAGE__ACCESS_KEY` and `STORAGE__SECRET_KEY`; the compose
file maps them onto the MinIO image's own variables, because the root user of MinIO *is*
its S3 access key:

```bash
# in docker/.env.dev or docker/.env.prod
STORAGE__ACCESS_KEY=hiresnap
STORAGE__SECRET_KEY=$(openssl rand -base64 32 | tr -d '/+=')
```

```yaml
# docker/docker-compose.yaml (shape, not final content)
environment:
  MINIO_ROOT_USER: ${STORAGE__ACCESS_KEY:?set STORAGE__ACCESS_KEY}
  MINIO_ROOT_PASSWORD: ${STORAGE__SECRET_KEY:?set STORAGE__SECRET_KEY}
```

Both are required with `:?`, and only for the `storage` service, so a base stack without
the profile neither needs nor sees them. The vendor pair `minioadmin`/`minioadmin` is what
MinIO invents if both are empty; it is acceptable only in dev, where the container
publishes nothing and sits on the no-egress network. In prod an unset key aborts the start
instead of shipping a well-known credential.

`STORAGE__ENDPOINT`, `STORAGE__BUCKET` and `STORAGE__SECURE` are **not** credentials and
not consumed by any container today; they are the declared contract for the S3 code path
the API does not have yet ([ENV.md §8.2](ENV.md#82-storage)).

Keys created *inside* MinIO for client applications are separate from the root
credential and should be per-consumer, revocable, and never used to boot the server.

## 6. Leak detection

Two layers, both cheap:

### 6.1 Pre-commit

`.pre-commit-config.yaml` (owned by the infra tooling) runs the repository guards plus
`gitleaks protect --staged`. The guards answer "is the env manifest consistent and is a
secret staged?" before the commit object exists, which is the only moment removal is
easy.

### 6.2 CI and on demand

```bash
gitleaks detect --source . --redact --verbose     # whole history
gitleaks detect --source . --redact --no-git      # working tree only
```

`.gitleaks.toml` extends the default ruleset and adds allow-list entries for paths that
legitimately contain key-shaped strings: `docs/` (examples in this file),
`infra/certs/.gitkeep` and `docker/.env.example` (placeholders). Allow-listing a
*path* is safer than allow-listing a *pattern*, because a pattern exemption eventually
matches real material.

Rules of thumb:

- A secret that reached a commit is compromised even if the commit was never pushed.
  Rotate it; do not rewrite history and call it fixed.
- `--redact` keeps the finding but not the value — always use it when the output might be
  pasted somewhere.
- `--no-git` is the fast check for a working tree you are about to share; the full scan is
  for CI, where history is available.

## 7. What is deliberately *not* a secret here

- `PUBLIC_BASE_URL` — a public hostname.
- `NGINX_PUBLISH_HTTP`, `NGINX_PUBLISH_HTTPS`, `BACKEND_PUBLISH_PORT`,
  `FRONTEND_PUBLISH_PORT` — ports.
- `DATA_PATH` — a path inside the container.
- `DEBUG` — a mode flag, though it is security-relevant: `TRUE` enables uvicorn
  `reload` **and** is the first half of the gate on the schema-creating `CreateSQL`
  run (`backend/service/main.py:72-79`, `:40-47`; the second half is `INIT_DB`). Never
  ship `TRUE` to production; it is treated as an environment mistake, not a secret.
- The content of `infra/storage/` — user data, protected by being ignored in git and by
  filesystem permissions, not by being a credential. It still must not be committed:
  `.gitignore` blocks it, and the reason is privacy, not key material.
