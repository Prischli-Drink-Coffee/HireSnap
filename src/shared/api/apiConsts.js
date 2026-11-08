// Base URL of the API, resolved once at module load.
//
// Precedence (first match wins):
//   1. process.env.REACT_APP_BASE_URL - explicit override, trimmed and forced to
//      end with "/" (give it the full API base, e.g. "https://example.com/api").
//      react-scripts substitutes the literal at BUILD time (DefinePlugin), so the
//      bundle carries the value the image was built with - see ARG
//      REACT_APP_BASE_URL in docker/Dockerfile.
//   2. window.location.origin + "/api/" - when the app is served from a
//      non-loopback host, so a reverse proxy in front of the bundle works without
//      a rebuild (the gateway rewrites "/api/" to the backend "/server/" mount).
//   3. the hardcoded local development base URL - unchanged behaviour when the
//      app is opened on localhost.
function readEnvBaseUrl() {
  try {
    return process.env.REACT_APP_BASE_URL || "";
  } catch (e) {
    return "";
  }
}

function resolveBaseUrl() {
  // 1. Explicit override; an empty or slash-only value is not a usable base, so
  // it falls through.
  let envBaseUrl = readEnvBaseUrl();
  if (typeof envBaseUrl === "string") {
    envBaseUrl = envBaseUrl.trim().replace(/\/+$/, "");
    if (envBaseUrl !== "") {
      return envBaseUrl + "/";
    }
  }
  // 2. Same-origin API path when the bundle is served from any non-loopback host.
  if (typeof window !== "undefined" && window.location && window.location.origin) {
    // Browsers report an IPv6 loopback host either bare ("::1") or bracketed.
    const host = (window.location.hostname || "").toLowerCase();
    const isLocalHost =
      host === "localhost" ||
      host === "127.0.0.1" ||
      host === "::1" ||
      host === "[::1]";
    if (host && !isLocalHost) {
      return window.location.origin + "/api/";
    }
  }
  // 3. Local development fallback.
  return "http://localhost:8080/api/";
}

export const baseUrl = resolveBaseUrl();

// Every path below is a route that exists in the backend today (each slice's
// backend/service/services/<domain>/presentation/routers/<name>_api/) and is reachable
// through the gateway, which
// rewrites "/api/" to the backend's "/server/" mount. Trailing slashes are kept
// exactly as the server declares them: FastAPI would answer a slashless POST
// with a 307 redirect, and a redirected multipart upload is a needless failure
// mode. Nothing here invents an endpoint.
export const authUrls = {
  signIn: "signin/",
  signUp: "signup/",
};

export const usersUrl = {
  users: "users/",
  byId: (userId) => `users/user_id/${userId}`,
  byEmail: (email) => `users/email/${encodeURIComponent(email)}`,
  update: (userId) => `users/${userId}`,
};

export const vacanciesUrl = {
  vacancies: "vacancies/",
  byId: (vacancyId) => `vacancies/vacancy_id/${vacancyId}`,
  update: (vacancyId) => `vacancies/${vacancyId}`,
  createWithInference: "upload_vacancy/inference/employer",
  recommendedCandidates: (vacancyId) =>
    `get_all_candidates/inference/vacancy/${vacancyId}`,
};

export const candidatesUrl = {
  uploadVideo: (userId) => `upload_video/inference/${userId}`,
  recommendedVacancies: (userId) =>
    `get_all_vacancies/inference/candidate/${userId}`,
};
