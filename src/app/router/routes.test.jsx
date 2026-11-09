import React from "react";
import { render, screen } from "@testing-library/react";
import { ChakraProvider } from "@chakra-ui/react";
import { CookiesProvider } from "react-cookie";
import Cookies from "js-cookie";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import routes from "app/router/routes";
import { ROUTE_PATHS, USER_ROLES } from "constants.js";

// The table itself. The guard suite already pins PrivateRoute in isolation, so
// what is left to prove is the WIRING: which path is behind which guard, and
// that a URL a user can type resolves to a screen instead of a blank tree.
//
// The pages are stubbed because each one owns its own tests; Layout, Header,
// NavBar and Footer are real, so the nested outlet structure is exercised.
jest.mock("pages/candidate/CandidatePage", () => ({
  __esModule: true,
  default: () => <p>candidate page</p>,
}));
jest.mock("pages/employer/EmployerPage", () => ({
  __esModule: true,
  default: () => <p>employer page</p>,
}));
jest.mock("pages/auth/SignInPage", () => ({
  __esModule: true,
  default: () => <p>sign-in page</p>,
}));
jest.mock("pages/auth/SignUpPage", () => ({
  __esModule: true,
  default: () => <p>sign-up page</p>,
}));
jest.mock("shared/hooks/useCurrentUser", () => ({
  __esModule: true,
  default: () => ({ user: null, loading: false }),
}));

const renderAt = (path) => {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  render(
    <CookiesProvider defaultSetOptions={{ path: "/" }}>
      <ChakraProvider>
        <RouterProvider router={router} />
      </ChakraProvider>
    </CookiesProvider>,
  );
};

const signIn = (role = "user") => {
  Cookies.set("role", role);
  Cookies.set("token", "access-token");
};

beforeEach(() => {
  ["token", "role", "userType", "userId", "userName"].forEach((name) => Cookies.remove(name));
});

test("the sign-in route is public", () => {
  renderAt(ROUTE_PATHS.signIn);

  expect(screen.getByText("sign-in page")).toBeInTheDocument();
});

test("an anonymous visitor at the root is sent to sign-in", () => {
  renderAt(ROUTE_PATHS.home);

  expect(screen.getByText("sign-in page")).toBeInTheDocument();
  expect(screen.queryByText("candidate page")).toBeNull();
});

test("a signed-in visitor at the root gets the candidate page", () => {
  signIn();
  renderAt(ROUTE_PATHS.home);

  expect(screen.getByText("candidate page")).toBeInTheDocument();
});

test("the employer route is behind the same any-identity guard", () => {
  signIn();
  renderAt(ROUTE_PATHS.employer);

  expect(screen.getByText("employer page")).toBeInTheDocument();
});

test("an anonymous visitor cannot reach the employer page", () => {
  renderAt(ROUTE_PATHS.employer);

  expect(screen.getByText("sign-in page")).toBeInTheDocument();
  expect(screen.queryByText("employer page")).toBeNull();
});

test("the signup route is closed to a signed-in non-administrator", () => {
  signIn("user");
  renderAt(ROUTE_PATHS.signUp);

  expect(screen.getByText("sign-in page")).toBeInTheDocument();
  expect(screen.queryByText("sign-up page")).toBeNull();
});

test("the signup route opens for the role the backend issues", () => {
  signIn(USER_ROLES.admin);
  renderAt(ROUTE_PATHS.signUp);

  expect(screen.getByText("sign-up page")).toBeInTheDocument();
});

test("every path a menu link points at resolves to a screen, not a blank outlet", () => {
  // navItems' paths come from the same constants object as the table
  // (constants.js:19-24), so this is the drift check: a path added to one and
  // not the other shows a black page and nothing else.
  const { navItems, authenticationNavItems } = jest.requireActual(
    "shared/components/layout/navItems",
  );
  const paths = [...navItems, ...authenticationNavItems]
    .map((item) => item.path)
    .filter(Boolean);

  expect(new Set(paths)).toEqual(
    new Set([ROUTE_PATHS.home, ROUTE_PATHS.employer, ROUTE_PATHS.signIn, ROUTE_PATHS.signUp]),
  );
});

test("the layout wraps every page with the site header and footer once", () => {
  signIn();
  renderAt(ROUTE_PATHS.home);

  expect(screen.getAllByRole("banner")).toHaveLength(1);
  expect(screen.getByText("candidate page")).toBeInTheDocument();
});
