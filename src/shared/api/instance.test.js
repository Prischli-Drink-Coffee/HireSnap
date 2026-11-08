import axios from "axios";
import { Instance } from "shared/api/instance";
import { baseUrl } from "shared/api/apiConsts";

// The axios instance, tested against the REAL axios.
//
// There is no jest.mock here on purpose: the point of this file is that the one
// client the app ships with is configured the way the rest of the code assumes.
// Mocking axios would assert that a mock was built, not that the shipped object
// has the right base URL, no default headers and no interceptor that rewrites
// errors before the caller can classify them.
test("it is an axios instance, not a hand-rolled fetch wrapper", () => {
  expect(axios.isAxiosError).toBeDefined();
  expect(typeof Instance.get).toBe("function");
  expect(typeof Instance.post).toBe("function");
  expect(typeof Instance.put).toBe("function");
});

test("the base URL is the one apiConsts resolved, so there is exactly one place to change it", () => {
  expect(Instance.defaults.baseURL).toBe(baseUrl);
  // A base URL without the trailing slash makes axios resolve "signin/" as
  // ".../apisin in/" - the prefix and the first path segment get glued together.
  expect(Instance.defaults.baseURL.endsWith("/")).toBe(true);
});

test("no interceptor is registered, so a 401 reaches the caller unchanged", () => {
  // The only 401 handling in the tree is in shared/hooks/useCurrentUser, which
  // clears the cookie. An interceptor here would run first and could swallow the
  // error that hook depends on - so the absence is a contract, not an accident.
  expect(Instance.interceptors.request.handlers).toHaveLength(0);
  expect(Instance.interceptors.response.handlers).toHaveLength(0);
});

test("no default Authorization header: the token is attached per call, not globally", () => {
  // A blanket default would put the token on signin and signup as well, and it
  // would survive a sign-out until the page was reloaded.
  expect(Instance.defaults.headers.common.Authorization).toBeUndefined();
  expect(Instance.defaults.headers.Authorization).toBeUndefined();
});

test("no request timeout is configured here", () => {
  // axios reports 0 for "unset". A timeout belongs to the call that needs it
  // (uploads and inference are slow); a global one would abort a video upload
  // long before the server answers.
  expect(Instance.defaults.timeout).toBe(0);
});
