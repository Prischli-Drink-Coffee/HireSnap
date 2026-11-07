// export const baseUrl = "http://192.168.31.178:8080/api/";
// Base URL precedence (first match wins):
//   1. process.env.REACT_APP_BASE_URL - explicit override, trimmed and forced to end with
//      "/" (give it the full API base, e.g. "https://hiresnap.example.com/api");
//   2. window.location.origin + "/api/" - when the app is served from a non-localhost host,
//      so a reverse proxy in front of the bundle works without a rebuild (the gateway
//      rewrites "/api/" to the backend "/server/" mount);
//   3. the hardcoded local development base URL ("http://localhost:8080/api/") - unchanged
//      behaviour when the app is opened on localhost.
//
// react-scripts (webpack DefinePlugin) substitutes the literal in readEnvBaseUrl at build
// time, so its try/catch only covers an environment with no `process` global at all (bare
// browser or SSR eval); it cannot alter the built bundle, where the value is inlined.
function readEnvBaseUrl() {
  try {
    return process.env.REACT_APP_BASE_URL || "";
  } catch (e) {
    return "";
  }
}

function resolveBaseUrl() {
  // 1. Explicit override; an empty or slash-only value is not a usable base, so it falls through.
  var envBaseUrl = readEnvBaseUrl();
  if (typeof envBaseUrl === "string") {
    envBaseUrl = envBaseUrl.trim().replace(/\/+$/, "");
    if (envBaseUrl !== "") {
      return envBaseUrl + "/";
    }
  }
  // 2. Same-origin API path when the bundle is served from any non-loopback host.
  if (typeof window !== "undefined" && window.location && window.location.origin) {
    // Browsers report an IPv6 loopback host either bare ("::1") or bracketed ("[::1]").
    var host = (window.location.hostname || "").toLowerCase();
    var isLocalHost = host === "localhost" || host === "127.0.0.1" || host === "::1" || host === "[::1]";
    if (host && !isLocalHost) {
      return window.location.origin + "/api/";
    }
  }
  // 3. Local development fallback - the original hardcoded constant.
  return "http://localhost:8080/api/";
}

export const baseUrl = resolveBaseUrl();
// export const baseUrl = "http://192.168.1.38/api/";
// export const baseUrl = "http://192.168.1.27:8080/api/";
// export const baseUrl = "http://192.168.145.121:8080/api/";
export const craftifyUrl = "tmcCraftifies";
export const deliveryMethodsUrl = "delivery_methods";
export const deliveryPlacesUrl = "delivery_places";
export const imagesUrl = "images";
export const materialsUrl = { materials: "materials", trim: "trim" };
export const propertiesUrl = "properties";
export const purchasesUrl = "purchases";
export const contractorsUrl = {
  contractors: "contractors",
};
export const tmcsUrl = "tmcs";
export const tmcTypesUrl = "tmctypes";
export const writeoffsUrl = "writeoffs";
export const warehousesUrl = {
  warehouses: "warehouses",
  update: "update_material",
  move: "move_to",
};
export const notificationsUrl = "notifications";
export const usersUrl = {
  users: "users",
  signIn: "signIn",
  signUp: "signUp",
  me: "me",
};
export const logsUrl = "logs";
