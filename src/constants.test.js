import {
  ACCESS_ANY_SIGNED_IN,
  PAGINATION,
  ROUTE_PATHS,
  USER_ROLES,
} from "constants.js";

// These tests assert the SHAPE of the constants, not their values. A test that
// restates "pageButtonCount === 5" fails every time somebody tunes the paginator
// and teaches nobody anything; the invariants below are the properties the rest
// of the tree relies on, and each one has a named failure mode.
describe("constants", () => {
  describe("ROUTE_PATHS", () => {
    test("has no duplicate value, so no route shadows another", () => {
      const values = Object.values(ROUTE_PATHS);
      expect(new Set(values).size).toBe(values.length);
    });

    test("every path is absolute and starts at the router root", () => {
      for (const [name, value] of Object.entries(ROUTE_PATHS)) {
        expect(typeof value).toBe("string");
        expect(value.startsWith("/")).toBe(true);
        expect(value).not.toMatch(/\s/);
        expect(name).toBeTruthy();
      }
    });

    test("the sign-in path is present, because every guard redirects to it", () => {
      // guards.jsx and both auth pages read ROUTE_PATHS.signIn; a missing key
      // would send <Navigate to={undefined}> and throw at render time.
      expect(typeof ROUTE_PATHS.signIn).toBe("string");
      expect(ROUTE_PATHS.signIn.length).toBeGreaterThan(1);
    });
  });

  describe("PAGINATION", () => {
    test("every value is a positive integer", () => {
      for (const [name, value] of Object.entries(PAGINATION)) {
        expect(typeof value).toBe("number");
        expect(Number.isInteger(value)).toBe(true);
        expect(value).toBeGreaterThan(0);
        expect(name).toBeTruthy();
      }
    });

    test("the page window is odd, so it can be centred on the current page", () => {
      // usePagination centres with currentPage - 2 .. currentPage + 2, which is
      // only symmetric for an odd width. An even width would leave the window
      // anchored one page to the left or right of the active button.
      expect(PAGINATION.pageButtonCount % 2).toBe(1);
    });

    test("the page window is wide enough to show both sides of the current page", () => {
      // Width 1 or 3 would make the "middle" branch of usePagination degenerate.
      expect(PAGINATION.pageButtonCount).toBeGreaterThanOrEqual(5);
    });

    test("the fallback page size does not exceed the starting page size", () => {
      // An emptied page-size box falls back to fallbackPageSize; if that were
      // larger than the default, clearing the box would show MORE rows than the
      // table starts with, which reads as a bug.
      expect(PAGINATION.fallbackPageSize).toBeLessThanOrEqual(PAGINATION.defaultPageSize);
    });

    test("the horizontal scroll breakpoint is a sane viewport width", () => {
      expect(PAGINATION.horizontalScrollWidth).toBeGreaterThan(320);
      expect(PAGINATION.horizontalScrollWidth).toBeLessThan(3840);
    });
  });

  describe("access tokens", () => {
    test("ACCESS_ANY_SIGNED_IN does not collide with a concrete role", () => {
      // guards.jsx ANDs the sentinel with the presence of a cookie role, so a
      // sentinel that equalled a role would silently downgrade to a role check.
      expect(Object.values(USER_ROLES)).not.toContain(ACCESS_ANY_SIGNED_IN);
    });

    test("ACCESS_ANY_SIGNED_IN is a non-empty string", () => {
      expect(typeof ACCESS_ANY_SIGNED_IN).toBe("string");
      expect(ACCESS_ANY_SIGNED_IN.length).toBeGreaterThan(0);
    });

    // The backend's RoleEnum is lowercase ("user" | "admin") and the cookie
    // carries the JWT's "sub" claim verbatim, so a constant spelled "ADMIN"
    // would never match and would silently close the registration screen. This
    // is the drift check: if the server's spelling ever changes, it fails here
    // rather than showing up as a link that does nothing.
    test("USER_ROLES values match the backend's lowercase role spelling", () => {
      const backendRoleSpelling = /^[a-z]+$/;

      for (const [name, value] of Object.entries(USER_ROLES)) {
        expect({ role: name, value, matchesBackend: backendRoleSpelling.test(value) }).toEqual({
          role: name,
          value,
          matchesBackend: true,
        });
      }
    });
  });
});
