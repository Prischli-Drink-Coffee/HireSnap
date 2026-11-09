import React from "react";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { CookiesProvider } from "react-cookie";
import PrivateRoute from "app/router/guards";
import { ACCESS_ANY_SIGNED_IN, ROUTE_PATHS, USER_ROLES } from "constants.js";

// PrivateRoute decides whether a route element renders at all, so the assertion
// that matters is "what is on screen", not what the component returned. Each
// case renders a real route table - a public sign-in route and one guarded
// child - then checks which of the two landed.
//
// The cookie is written to document.cookie rather than injected into the
// provider, because that is the path the browser takes: universal-cookie reads
// document.cookie on every get, so a real cookie is what the guard will see in
// production. cjs/../utils.js hasDocumentCookie() also accepts a
// global.TEST_HAS_DOCUMENT_COOKIE override; a real cookie needs no test hook.
const setCookies = (jar) => {
  for (const [name, value] of Object.entries(jar)) {
    document.cookie = `${name}=${value}; path=/`;
  }
};

const clearCookies = () => {
  for (const pair of document.cookie.split(";")) {
    const name = pair.split("=")[0].trim();
    if (name) document.cookie = `${name}=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT`;
  }
};

const renderGuarded = (userGroup, initialEntries, jar = {}) => {
  setCookies(jar);

  render(
    <CookiesProvider defaultSetOptions={{ path: "/" }}>
      <MemoryRouter initialEntries={initialEntries}>
        <Routes>
          <Route path={ROUTE_PATHS.signIn} element={<p>sign-in page</p>} />
          <Route element={<PrivateRoute userGroup={userGroup} />}>
            <Route path="/protected" element={<p>protected content</p>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </CookiesProvider>,
  );
};

const expectRedirectedToSignIn = () => {
  expect(screen.getByText("sign-in page")).toBeInTheDocument();
  expect(screen.queryByText("protected content")).not.toBeInTheDocument();
};

const expectAllowed = () => {
  expect(screen.getByText("protected content")).toBeInTheDocument();
  expect(screen.queryByText("sign-in page")).not.toBeInTheDocument();
};

describe("PrivateRoute", () => {
  afterEach(clearCookies);

  test("sends an anonymous visitor to the sign-in page", () => {
    renderGuarded(ACCESS_ANY_SIGNED_IN, ["/protected"]);

    expectRedirectedToSignIn();
  });

  test("renders the child for any signed-in identity when the group is the any-identity sentinel", () => {
    // "AUTH" means "somebody is logged in", so even a role the app does not
    // enumerate - here the lowercase spelling the backend actually issues -
    // passes.
    renderGuarded(ACCESS_ANY_SIGNED_IN, ["/protected"], { role: "candidate" });

    expectAllowed();
  });

  test("renders the child when the cookie role equals the requested group exactly", () => {
    renderGuarded("ADMIN", ["/protected"], { role: "ADMIN" });

    expectAllowed();
  });

  test("redirects when the cookie role differs from the requested group", () => {
    renderGuarded("ADMIN", ["/protected"], { role: "user" });

    expectRedirectedToSignIn();
  });

  // The guard compares the cookie to the constant by strict equality, so this
  // case is the one that used to fail: USER_ROLES.admin was "ADMIN" while the
  // backend issues lowercase, and every real administrator was bounced off the
  // registration screen. constants.test.js pins the spelling; this pins that the
  // guard then opens.
  test("opens an ADMIN route for the lowercase role the backend issues", () => {
    renderGuarded(USER_ROLES.admin, ["/protected"], { role: "admin" });

    expectAllowed();
  });

  // Defensive: a route author who forgets the prop gets a closed door, not an
  // open one, because undefined equals neither the sentinel nor a role.
  test("keeps the door closed when the guard is given no group at all", () => {
    renderGuarded(undefined, ["/protected"], { role: "ADMIN" });

    expectRedirectedToSignIn();
  });
});
