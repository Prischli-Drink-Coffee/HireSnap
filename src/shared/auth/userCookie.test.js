import Cookies from "js-cookie";
import {
  deleteUser,
  getRole,
  getToken,
  getUserId,
  setUser,
} from "shared/auth/userCookie";

// The cookie cache of the signed-in identity.
//
// The real js-cookie is used against jsdom's document.cookie, so these are round
// trips, not assertions on a mock. js-cookie 3 reads document.cookie on every
// get, which is what lets a test write a raw cookie and read it back the way the
// browser would.
const ALL_NAMES = ["token", "role", "userType", "userId", "userName"];

const clearAll = () => {
  ALL_NAMES.forEach((name) => Cookies.remove(name));
};

beforeEach(() => {
  clearAll();
  document.cookie = "";
});

afterAll(() => {
  clearAll();
});

describe("getToken", () => {
  test("returns undefined when no token is cached", () => {
    expect(getToken()).toBeUndefined();
  });

  test("prefixes the cached value with Bearer", () => {
    Cookies.set("token", "abc.def.ghi");
    expect(getToken()).toBe("Bearer abc.def.ghi");
  });

  test("does not double the prefix when the cookie already carries one", () => {
    // setUser stores whatever the token endpoint returned, which is the bare
    // token; this pins that the prefix is added once, at read time.
    Cookies.set("token", "Bearer abc");
    expect(getToken()).toBe("Bearer Bearer abc");
  });
});

describe("getRole and getUserId", () => {
  test("getRole is undefined before sign-in and the raw string after", () => {
    expect(getRole()).toBeUndefined();
    Cookies.set("role", "candidate");
    expect(getRole()).toBe("candidate");
  });

  test("getUserId converts the cookie string to a number", () => {
    Cookies.set("userId", "42");
    expect(getUserId()).toBe(42);
  });

  test("getUserId is undefined when the cookie is absent, not NaN", () => {
    // undefined is the signal Layout and CandidatePage test for; NaN would be a
    // different story and Number(undefined) would produce it.
    expect(getUserId()).toBeUndefined();
    expect(Number.isNaN(getUserId())).toBe(false);
  });

  test("a malformed userId degrades to NaN, which every caller treats as absent", () => {
    // Pinned, not endorsed: getUserId does Number(value) without a guard. NaN is
    // falsy, so `!user?.userId` still disables an upload instead of pointing it
    // at /upload_video/inference/NaN.
    Cookies.set("userId", "not-a-number");
    expect(Number.isNaN(getUserId())).toBe(true);
  });
});

describe("setUser", () => {
  test("writes all five fields and reads them back", () => {
    setUser({
      token: "abc",
      role: "candidate",
      userType: "candidate",
      userId: 42,
      userName: "user@example.com",
    });

    expect(getToken()).toBe("Bearer abc");
    expect(getRole()).toBe("candidate");
    expect(getUserId()).toBe(42);
    expect(Cookies.get("userType")).toBe("candidate");
    expect(Cookies.get("userName")).toBe("user@example.com");
  });

  test("accepts accessToken as the token, so the sign-in response can be passed through", () => {
    // POST /signin/ answers {access_token, refresh_token, token_type}; the pages
    // hand that pair straight to sessionFromTokens and then to setUser.
    setUser({ accessToken: "from-access-token" });
    expect(getToken()).toBe("Bearer from-access-token");
  });

  test("token wins over accessToken when both are given", () => {
    setUser({ token: "winner", accessToken: "loser" });
    expect(Cookies.get("token")).toBe("winner");
  });

  test("skips undefined fields instead of writing the string 'undefined'", () => {
    setUser({ token: "abc", role: undefined, userId: undefined });
    expect(Cookies.get("role")).toBeUndefined();
    expect(Cookies.get("userId")).toBeUndefined();
  });

  test("is additive: a field the caller omits keeps its previous value", () => {
    // Both call sites rely on this - Layout writes the role, the pages write the
    // identity - so a partial update must not blank the rest.
    setUser({ token: "abc", role: "admin" });
    setUser({ userId: 7 });
    expect(getRole()).toBe("admin");
    expect(getToken()).toBe("Bearer abc");
    expect(getUserId()).toBe(7);
  });

  test("writes userId 0, which is why the guard tests for undefined and null only", () => {
    setUser({ userId: 0 });
    expect(Cookies.get("userId")).toBe("0");
    expect(getUserId()).toBe(0);
  });

  test("userId null is treated as absent", () => {
    setUser({ userId: null });
    expect(Cookies.get("userId")).toBeUndefined();
  });

  test("every write carries the decade-long expiry, so the cookie survives a restart", () => {
    // The expiry is the module's only lifetime policy: the ACCESS token decides
    // when the session ends, and a shorter cookie would log the user out of the
    // UI before the token was actually rejected.
    const spy = jest.spyOn(Cookies, "set");
    setUser({ token: "abc", role: "user", userType: "candidate", userId: 1, userName: "a@b.c" });

    expect(spy).toHaveBeenCalledTimes(5);
    spy.mock.calls.forEach(([name, value, options]) => {
      expect(typeof name).toBe("string");
      expect(value).toBeDefined();
      expect(options).toEqual({ expires: 365 ** 2 });
    });
    spy.mockRestore();
  });
});

describe("deleteUser", () => {
  test("removes every cached field", () => {
    setUser({ token: "abc", role: "user", userType: "candidate", userId: 1, userName: "a@b.c" });
    deleteUser();

    ALL_NAMES.forEach((name) => {
      expect(Cookies.get(name)).toBeUndefined();
    });
  });

  test("is safe to call when nothing is cached", () => {
    expect(() => deleteUser()).not.toThrow();
    expect(getToken()).toBeUndefined();
  });

  test("leaves unrelated cookies alone", () => {
    // The app keeps no other cookie today, but a blanket document.cookie reset
    // would quietly break anything added later.
    Cookies.set("unrelated", "keep-me");
    setUser({ token: "abc" });
    deleteUser();

    expect(Cookies.get("unrelated")).toBe("keep-me");
  });
});
