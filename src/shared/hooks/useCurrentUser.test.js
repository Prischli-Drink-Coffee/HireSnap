import React from "react";
import { CookiesProvider } from "react-cookie";
import Cookies from "js-cookie";
import { renderHook, waitFor } from "@testing-library/react";
import useCurrentUser from "shared/hooks/useCurrentUser";
import UserService from "shared/api/services/userService";

// This hook holds the only 401 handling in the tree, so the assertions below are
// about consequences, not about the shape of the returned object: a 401 must drop
// the cached identity (otherwise the router loops on a dead token), and anything
// else must leave it alone.
jest.mock("shared/api/services/userService", () => ({
  __esModule: true,
  default: { getById: jest.fn() },
}));

const wrapper = ({ children }) => <CookiesProvider>{children}</CookiesProvider>;

const mountHook = () => renderHook(() => useCurrentUser(), { wrapper });

const signCookies = (values) => {
  Object.entries(values).forEach(([name, value]) => Cookies.set(name, value));
  return values;
};

beforeEach(() => {
  // resetMocks:true wipes the factory implementations before every test, and the
  // jsdom cookie jar survives the whole file, so both have to be reset by hand.
  UserService.getById.mockReset();
  ["token", "role", "userType", "userId", "userName"].forEach((name) => Cookies.remove(name));
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  jest.restoreAllMocks();
});

test("with no token cookie there is no user, no loading state and no request", () => {
  const { result } = mountHook();

  expect(result.current.user).toBeNull();
  expect(result.current.loading).toBe(false);
  expect(UserService.getById).not.toHaveBeenCalled();
});

test("with a token but no user id it does not guess an identity", () => {
  // Right after sign-in the pair lands before the id does; asking for user
  // "undefined" would be a 404 on every mount.
  signCookies({ token: "access-token" });
  const { result } = mountHook();

  expect(result.current.loading).toBe(false);
  expect(result.current.user).toBeNull();
  expect(UserService.getById).not.toHaveBeenCalled();
});

test("resolves the profile by the numeric id from the cookie", async () => {
  signCookies({ token: "access-token", userId: "7" });
  UserService.getById.mockResolvedValue({
    data: { id: 7, role: "user", type: "candidate", email: "ada@example.com" },
  });

  const { result } = mountHook();

  await waitFor(() => expect(result.current.loading).toBe(false));
  expect(UserService.getById).toHaveBeenCalledTimes(1);
  expect(UserService.getById).toHaveBeenCalledWith(7);
  expect(result.current.user).toEqual({
    id: 7,
    role: "user",
    type: "candidate",
    email: "ada@example.com",
  });
});

test("mirrors the profile into the cookies every other component reads", async () => {
  signCookies({ token: "access-token", userId: "7" });
  UserService.getById.mockResolvedValue({
    data: { id: 7, role: "admin", type: "employer", email: "boss@example.com" },
  });

  const { result } = mountHook();
  await waitFor(() => expect(result.current.loading).toBe(false));

  expect(Cookies.get("role")).toBe("admin");
  expect(Cookies.get("userType")).toBe("employer");
  expect(Cookies.get("userId")).toBe("7");
  expect(Cookies.get("userName")).toBe("boss@example.com");
  expect(Cookies.get("token")).toBe("access-token");
});

test("a 401 clears the cached session, so the router can leave the page", async () => {
  signCookies({ token: "stale-token", userId: "7", role: "user", userName: "ada@example.com" });
  UserService.getById.mockRejectedValue({ response: { status: 401 } });

  const { result } = mountHook();

  await waitFor(() => expect(result.current.loading).toBe(false));
  expect(Cookies.get("token")).toBeUndefined();
  expect(Cookies.get("role")).toBeUndefined();
  expect(Cookies.get("userId")).toBeUndefined();
  expect(Cookies.get("userName")).toBeUndefined();
  expect(result.current.user).toBeNull();
});

test("a network failure does not clear the session", async () => {
  // An offline moment, a 500, a DNS blip: the token is still valid, so throwing
  // the user out would be a logout nobody asked for.
  signCookies({ token: "good-token", userId: "7", role: "user" });
  UserService.getById.mockRejectedValue(new Error("Network Error"));

  const { result } = mountHook();

  await waitFor(() => expect(result.current.loading).toBe(false));
  expect(Cookies.get("token")).toBe("good-token");
  expect(Cookies.get("role")).toBe("user");
  expect(result.current.user).toBeNull();
});

test("a non-401 HTTP error is treated as transient too", async () => {
  signCookies({ token: "good-token", userId: "7" });
  UserService.getById.mockRejectedValue({ response: { status: 503 } });

  const { result } = mountHook();

  await waitFor(() => expect(result.current.loading).toBe(false));
  expect(Cookies.get("token")).toBe("good-token");
});

test("logs the failure instead of failing silently", async () => {
  signCookies({ token: "good-token", userId: "7" });
  const failure = new Error("Network Error");
  UserService.getById.mockRejectedValue(failure);

  mountHook();

  await waitFor(() =>
    expect(console.error).toHaveBeenCalledWith("Error loading current user:", failure),
  );
});

test("two simultaneous mounts share one request", async () => {
  // Layout and the page below both call the hook; two identical GETs per
  // navigation is the bug the module-level in-flight promise exists to prevent.
  signCookies({ token: "access-token", userId: "7" });
  let resolveProfile;
  UserService.getById.mockReturnValue(
    new Promise((resolve) => {
      resolveProfile = resolve;
    }),
  );

  // Two hook instances, as Layout and the page below create them. The lint rule
  // wants render results destructured, so each view is unpacked immediately.
  const { result: firstResult } = mountHook();
  const { result: secondResult } = mountHook();

  expect(UserService.getById).toHaveBeenCalledTimes(1);

  resolveProfile({ data: { id: 7, role: "user", type: "candidate", email: "ada@example.com" } });
  await waitFor(() => expect(firstResult.current.loading).toBe(false));
  await waitFor(() => expect(secondResult.current.loading).toBe(false));
  expect(UserService.getById).toHaveBeenCalledTimes(1);
});
