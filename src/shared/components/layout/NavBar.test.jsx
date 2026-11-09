import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { ChakraProvider } from "@chakra-ui/react";
import { CookiesProvider } from "react-cookie";
import Cookies from "js-cookie";
import { MemoryRouter } from "react-router-dom";
import NavBar from "shared/components/layout/NavBar";
import { USER_ROLES } from "constants.js";

// The drawer is the only place that reads the role cookie to decide what is
// visible. It is cosmetic - every route and endpoint checks the JWT - but a
// wrong menu is the first thing a user reports, so the role matrix is pinned
// here rather than left to a manual click-through.
const renderNavBar = () =>
  render(
    <ChakraProvider>
      <CookiesProvider>
        <MemoryRouter>
          <NavBar />
        </MemoryRouter>
      </CookiesProvider>
    </ChakraProvider>,
  );

beforeEach(() => {
  ["token", "role", "userType", "userId", "userName"].forEach((name) => Cookies.remove(name));
});

test("shows only the sign-in entry to a visitor", () => {
  renderNavBar();

  expect(screen.getByRole("link", { name: "Вход" })).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Главная" })).toBeNull();
  expect(screen.queryByRole("link", { name: "Выход" })).toBeNull();
  expect(screen.queryByRole("link", { name: "Регистрация" })).toBeNull();
});

test("shows the signed-in entries once a role cookie exists", () => {
  Cookies.set("role", "user");
  Cookies.set("userName", "ada@example.com");
  renderNavBar();

  expect(screen.getByRole("link", { name: "Главная" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Работодателю" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Выход" })).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Вход" })).toBeNull();
  expect(screen.queryByRole("link", { name: "Регистрация" })).toBeNull();
});

test("renders the user's name as text, not as a link", () => {
  Cookies.set("role", "user");
  Cookies.set("userName", "ada@example.com");
  renderNavBar();

  expect(screen.getByText("ada@example.com")).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "ada@example.com" })).toBeNull();
});

test("offers registration to the role constant it gates on", () => {
  Cookies.set("role", USER_ROLES.admin);
  renderNavBar();

  expect(screen.getByRole("link", { name: "Регистрация" })).toBeInTheDocument();
});

test("hides registration from a non-administrator role", () => {
  Cookies.set("role", "candidate");
  renderNavBar();

  expect(screen.queryByRole("link", { name: "Регистрация" })).toBeNull();
});

test("offers registration to the lowercase role the backend actually issues", () => {
  // The cookie holds the JWT's "sub" claim verbatim and the backend's RoleEnum
  // is lowercase, so "admin" - not the constant's spelling - is what a real
  // administrator carries. This is the end-to-end proof that the two agree.
  Cookies.set("role", "admin");
  renderNavBar();

  expect(screen.getByRole("link", { name: "Регистрация" })).toBeInTheDocument();
});

test("signing out is a click that clears the cached identity", () => {
  Cookies.set("role", "user");
  Cookies.set("token", "access-token");
  Cookies.set("userId", "7");
  renderNavBar();

  fireEvent.click(screen.getByRole("link", { name: "Выход" }));

  expect(Cookies.get("token")).toBeUndefined();
  expect(Cookies.get("role")).toBeUndefined();
  expect(Cookies.get("userId")).toBeUndefined();
});

test("the menu links are relative paths, so the hash router owns them", () => {
  Cookies.set("role", "user");
  renderNavBar();

  expect(screen.getByRole("link", { name: "Главная" })).toHaveAttribute("href", "/");
  expect(screen.getByRole("link", { name: "Работодателю" })).toHaveAttribute("href", "/employer");
});
