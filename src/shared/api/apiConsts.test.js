import {
  authUrls,
  candidatesUrl,
  usersUrl,
  vacanciesUrl,
} from "shared/api/apiConsts";

// The API base URL and the route table.
//
// resolveBaseUrl() is deliberately NOT exported: it runs once at module load and
// the rest of the app only ever sees the frozen "baseUrl". So the three-step
// precedence is pinned by re-requiring the module inside jest.isolateModules
// with a stubbed window.location and process.env - the same inputs the function
// reads at load time. Reading it any other way would test a copy.
const REAL_LOCATION = window.location;

const stubLocation = ({ origin, hostname }) => {
  delete window.location;
  window.location = { origin, hostname };
};

const loadBaseUrl = () => {
  let loaded;
  jest.isolateModules(() => {
    ({ baseUrl: loaded } = require("shared/api/apiConsts"));
  });
  return loaded;
};

const REMOTE = { origin: "https://app.example.com", hostname: "app.example.com" };

beforeEach(() => {
  stubLocation(REMOTE);
  delete process.env.REACT_APP_BASE_URL;
});

afterEach(() => {
  delete process.env.REACT_APP_BASE_URL;
  Object.defineProperty(window, "location", {
    configurable: true,
    writable: true,
    value: REAL_LOCATION,
  });
});

describe("resolveBaseUrl precedence", () => {
  test("step 1: REACT_APP_BASE_URL wins over the served host and gains one trailing slash", () => {
    process.env.REACT_APP_BASE_URL = "https://api.example.com/api";
    expect(loadBaseUrl()).toBe("https://api.example.com/api/");
  });

  test("step 1: trailing slashes are collapsed to exactly one", () => {
    process.env.REACT_APP_BASE_URL = "https://api.example.com/api///";
    expect(loadBaseUrl()).toBe("https://api.example.com/api/");
  });

  test("step 1: surrounding whitespace is trimmed", () => {
    // react-scripts inlines the build ARG verbatim, so a value pasted into a
    // compose file can arrive padded; a padded base URL produces "https://x /api/".
    process.env.REACT_APP_BASE_URL = "  https://api.example.com/api  ";
    expect(loadBaseUrl()).toBe("https://api.example.com/api/");
  });

  test("step 1: an empty value falls through to the host", () => {
    process.env.REACT_APP_BASE_URL = "";
    expect(loadBaseUrl()).toBe("https://app.example.com/api/");
  });

  test("step 1: a slash-only value is not a base and falls through", () => {
    process.env.REACT_APP_BASE_URL = "/";
    expect(loadBaseUrl()).toBe("https://app.example.com/api/");
  });

  test("step 2: a non-loopback host uses the same origin plus /api/", () => {
    expect(loadBaseUrl()).toBe("https://app.example.com/api/");
  });

  test("step 3: localhost keeps the hardcoded development URL", () => {
    stubLocation({ origin: "http://localhost:3000", hostname: "localhost" });
    expect(loadBaseUrl()).toBe("http://localhost:8080/api/");
  });

  test("step 3: 127.0.0.1 is loopback too", () => {
    stubLocation({ origin: "http://127.0.0.1:3000", hostname: "127.0.0.1" });
    expect(loadBaseUrl()).toBe("http://localhost:8080/api/");
  });

  test("step 3: both spellings of the IPv6 loopback are loopback", () => {
    // Browsers report the host either bare or bracketed depending on the engine.
    stubLocation({ origin: "http://[::1]:3000", hostname: "[::1]" });
    expect(loadBaseUrl()).toBe("http://localhost:8080/api/");

    stubLocation({ origin: "http://[::1]:3000", hostname: "::1" });
    expect(loadBaseUrl()).toBe("http://localhost:8080/api/");
  });

  test("step 3: the hostname comparison is case-insensitive", () => {
    stubLocation({ origin: "http://LocalHost:3000", hostname: "LocalHost" });
    expect(loadBaseUrl()).toBe("http://localhost:8080/api/");
  });

  test("step 3: an origin with no hostname falls through instead of building a broken URL", () => {
    stubLocation({ origin: "", hostname: "" });
    expect(loadBaseUrl()).toBe("http://localhost:8080/api/");
  });
});

describe("route table", () => {
  // Spelled out rather than derived from Object.values: usersUrl and friends mix
  // plain strings ("users/") with builder functions, and a blanket map would try
  // to call a string.
  const everyPath = () => [
    authUrls.signIn,
    authUrls.signUp,
    usersUrl.users,
    usersUrl.byId(1),
    usersUrl.byEmail("a@b.c"),
    usersUrl.update(1),
    vacanciesUrl.vacancies,
    vacanciesUrl.byId(1),
    vacanciesUrl.update(1),
    vacanciesUrl.createWithInference,
    vacanciesUrl.recommendedCandidates(1),
    candidatesUrl.uploadVideo(1),
    candidatesUrl.recommendedVacancies(1),
  ];

  test("every path is relative, because a leading slash would escape the /api/ prefix", () => {
    // axios resolves a relative path against baseURL (".../api/"); a path that
    // starts with "/" resolves against the ORIGIN instead and the gateway prefix
    // silently disappears - a 404 that looks like a missing backend route.
    const paths = everyPath();

    expect(paths.length).toBe(13);
    paths.forEach((path) => {
      expect(path.startsWith("/")).toBe(false);
      expect(path.trim()).toBe(path);
    });
  });

  test("no path repeats the api or server prefix the base URL and the gateway already add", () => {
    everyPath().forEach((path) => {
      expect(path).not.toMatch(/^api\//);
      expect(path).not.toMatch(/^server\//);
    });
  });

  test("the paths match the routers the backend actually serves", () => {
    // Each line is copied from a router module; if one moves, this test and the
    // service test fail before a browser ever sends the request.
    //   backend/service/services/auth/presentation/routers/auth_api/auth_api.py:33,51
    //   backend/service/services/users/presentation/routers/users_api/users_api.py:36,50,66,103
    //   backend/service/services/vacancies/presentation/routers/vacancies_api/vacancies_api.py:35,49,80
    //   backend/service/services/matching/presentation/routers/inference_api/inference_api.py:40,65,88,105
    expect(authUrls).toEqual({ signIn: "signin/", signUp: "signup/" });
    expect(usersUrl.users).toBe("users/");
    expect(usersUrl.byId(12)).toBe("users/user_id/12");
    expect(usersUrl.byEmail("a@b.c")).toBe("users/email/a%40b.c");
    expect(usersUrl.update(12)).toBe("users/12");
    expect(vacanciesUrl.vacancies).toBe("vacancies/");
    expect(vacanciesUrl.byId(12)).toBe("vacancies/vacancy_id/12");
    expect(vacanciesUrl.update(12)).toBe("vacancies/12");
    expect(vacanciesUrl.createWithInference).toBe(
      "upload_vacancy/inference/employer",
    );
    expect(vacanciesUrl.recommendedCandidates(12)).toBe(
      "get_all_candidates/inference/vacancy/12",
    );
    expect(candidatesUrl.uploadVideo(12)).toBe("upload_video/inference/12");
    expect(candidatesUrl.recommendedVacancies(12)).toBe(
      "get_all_vacancies/inference/candidate/12",
    );
  });

  test("an email is percent-encoded, so a plus or a slash cannot break the segment", () => {
    expect(usersUrl.byEmail("first.last+tag@example.com")).toBe(
      "users/email/first.last%2Btag%40example.com",
    );
    expect(usersUrl.byEmail("a/b")).toBe("users/email/a%2Fb");
  });

  test("ids are interpolated verbatim, with no encoding and no trailing slash added", () => {
    expect(vacanciesUrl.recommendedCandidates(0)).toBe(
      "get_all_candidates/inference/vacancy/0",
    );
    expect(candidatesUrl.uploadVideo("42")).toBe("upload_video/inference/42");
  });
});
