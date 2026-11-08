"""The FastAPI application: mounts, middleware, routers and the health route.

WHAT IS PRESERVED FROM `src/pipeline/server.py` (byte-for-byte in behaviour):

  * the three mounts and their exact prefixes - `/server` (the real API),
    `/public` (declared, currently empty) and `/data` (StaticFiles over
    `DATA_PATH`). The gateway rewrites `/api/` to `/server/`, and
    scripts/check_api_prefix_contract.py asserts that pairing.
  * CORS defaults to `allow_origins=["*"]`, the pre-existing (too permissive)
    behaviour. It is deliberately NOT tightened by default: changing it is a product
    decision about which origins are legitimate and it would break the development
    flow that relies on it. `CORS_ALLOW_ORIGINS` now lets a deployment tighten it
    without a code change - see `_cors_settings()`.
  * the OpenAPI tag list and its order, so `/server/docs` looks the same.
  * construction order: middleware, then mounts, then routers.

WHAT IS NEW: `GET /server/health`. It answers with the same three keys the ml
sidecar uses (docs/SERVICE_CONTRACTS.md 1.1) and touches nothing - no MySQL, no
cache, no storage - so it is a liveness probe, not a readiness probe. Both compose
files, the nginx gateway and backend/docker/Dockerfile probe this route
(`docker/docker-compose.yaml` healthcheck, `backend/docker/Dockerfile` HEALTHCHECK);
`/server/openapi.json` also answers, because that proves the StaticFiles mount was
constructed, but nothing depends on it any more.
"""

from __future__ import annotations

import json
import os

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.models import Tag as OpenApiTag
from fastapi.staticfiles import StaticFiles

from service import __version__
from service.api.routers import (
    auth,
    candidate_vacancies,
    embeddings,
    files,
    info_candidates,
    inference,
    magics,
    personalities,
    personality_candidates,
    personality_vacancies,
    users,
    vacancies,
)
from service.configuration.logging import get_logger
from service.configuration.paths import data_dir

log = get_logger()

# --- OpenAPI tags -----------------------------------------------------------
# Order is the order the old module declared, so the docs page keeps its shape.
OPTIONS_TAG = "Main"
TAGS = [
    OpenApiTag(name="Main", description="CRUD operations main"),
    OpenApiTag(name="User", description="CRUD operations user"),
    OpenApiTag(name="InfoCandidate", description="CRUD operations info candidate"),
    OpenApiTag(name="Magic", description="CRUD operations magic"),
    OpenApiTag(name="Vacancy", description="CRUD operations vacancy"),
    OpenApiTag(name="Personality", description="CRUD operations personality"),
    OpenApiTag(name="PersonalityVacancy", description="CRUD operations personality vacancy"),
    OpenApiTag(name="PersonalityCandidate", description="CRUD operations personality candidate"),
    OpenApiTag(name="CandidateVacancy", description="CRUD operations candidate vacancy"),
    OpenApiTag(name="File", description="CRUD operations file"),
    OpenApiTag(name="Embedding", description="CRUD operations embedding"),
]

#: The routers, in the order the old single module declared their handlers. Order
#: matters for two reasons: OpenAPI output is generated in declaration order, and
#: a static path must be declared before a parameterised one that could shadow it.
ROUTERS = (
    auth,
    inference,
    users,
    info_candidates,
    vacancies,
    magics,
    personalities,
    personality_vacancies,
    personality_candidates,
    candidate_vacancies,
    files,
    embeddings,
)


def _cors_settings() -> dict:
    """Resolve CORS from the environment, defaulting to the legacy wildcard.

    WHY this exists at all: `CORS_ALLOW_ORIGINS` was declared in the env manifest
    (docker/.env.example) and read by nobody - the middleware hardcoded
    `allow_origins=["*"]`. A documented knob that nothing reads is worse than no
    knob: an operator who sets it believes the surface narrowed, and it did not.

    THE SHAPE AND THE RULE:

      * unset, empty or `*` -> the legacy behaviour, `allow_origins=["*"]`. This
        branch is what keeps every existing deployment byte-identical, and the
        test suite pins it.
      * a JSON array (`["https://a.example","https://b.example"]`) or a
        comma-separated list (`https://a.example,https://b.example`) -> exactly
        those origins.

    WHY credentials are forced off for the wildcard: the browser rejects
    `Access-Control-Allow-Origin: *` together with
    `Access-Control-Allow-Credentials: true`, and Starlette would emit both. The
    old code shipped that pair, so a credentialed cross-origin request from any
    origin never actually worked; this only stops pretending. With an explicit
    list the pair IS legal, so credentials stay on - which is the configuration a
    real deployment should use.

    WHERE the result goes: this returns keyword arguments for CORSMiddleware. It
    is a function and not a module constant because the value depends on the
    environment, and importing this module must not require one.
    """
    raw = os.environ.get("CORS_ALLOW_ORIGINS", "").strip()
    if not raw:
        return {"allow_origins": ["*"], "allow_credentials": False}

    origins: list[str] = []
    if raw.startswith("["):
        # Tolerate the JSON form quoted by Compose (a single-quoted value arrives
        # with the brackets intact) and ignore anything malformed rather than
        # crashing at import: a bad CORS value must not make the API unimportable.
        try:
            parsed = json.loads(raw)
        except ValueError:
            log.warning("CORS_ALLOW_ORIGINS is not valid JSON, falling back to '*': %r", raw)
            return {"allow_origins": ["*"], "allow_credentials": False}
        if isinstance(parsed, list):
            origins = [str(item).strip() for item in parsed if str(item).strip()]
    else:
        origins = [part.strip() for part in raw.split(",") if part.strip()]

    if not origins or origins == ["*"]:
        return {"allow_origins": ["*"], "allow_credentials": False}
    return {"allow_origins": origins, "allow_credentials": True}


def build_server_app() -> FastAPI:
    """The application mounted at /server: every business route lives here."""
    app_server = FastAPI(title="API - server")
    app_server.openapi_tags = [tag.model_dump() for tag in TAGS]

    @app_server.get("/health", tags=[OPTIONS_TAG], summary="Liveness probe")
    async def health() -> dict:
        """Three keys, no I/O. Mirrors GET /health on the ml sidecar."""
        return {"status": "ok", "service": "backend", "version": __version__}

    for module in ROUTERS:
        app_server.include_router(module.router)
    return app_server


def build_public_app() -> FastAPI:
    """The application mounted at /public.

    It has no routes today and that is on purpose: the mount is part of the public
    URL contract (the frontend and the gateway probes reference /api/ and /server/,
    and /public is reserved for endpoints that must work without a token). Removing
    the mount would be a URL change, not a cleanup.
    """
    app_public = FastAPI(title="API - public")
    app_public.openapi_tags = [
        OpenApiTag(name=OPTIONS_TAG, description="CRUD operations main").model_dump()
    ]
    return app_public


def create_app() -> FastAPI:
    """Build the root application. Safe to call with no database and no .env."""
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_methods=["*"],
        allow_headers=["*"],
        **_cors_settings(),
    )
    app.mount("/server", build_server_app())
    app.mount("/public", build_public_app())
    # StaticFiles over the uploads directory. The directory is created here if it
    # is missing: StaticFiles refuses to construct for a non-existent path, and a
    # fresh checkout (or a container whose bind mount is not there yet) would then
    # fail to import - the exact class of failure this refactor removes.
    uploads = data_dir()
    try:
        os.makedirs(uploads, exist_ok=True)
    except OSError as exc:  # pragma: no cover - read-only filesystem
        log.warning("could not create DATA_PATH %s: %s", uploads, exc)
    app.mount("/data", StaticFiles(directory=str(uploads)), name="data")
    return app


#: Module-level application, so `uvicorn service.api.app:app` works and a reloader
#: that re-imports this module gets the same object shape.
app = create_app()
