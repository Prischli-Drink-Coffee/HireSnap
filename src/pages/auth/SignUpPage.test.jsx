import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ChakraProvider } from "@chakra-ui/react";
import { CookiesProvider } from "react-cookie";
import Cookies from "js-cookie";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import SignUpPage from "pages/auth/SignUpPage";
import UserService from "shared/api/services/userService";
import { ROUTE_PATHS } from "constants.js";

// Registration is the only screen that sends type_user, and the server rejects
// anything that is not "candidate" or "employer". So the payload shape is the
// interesting part; the rest is the usual form behaviour.
jest.mock("shared/api/services/userService", () => ({
  __esModule: true,
  default: { signUp: jest.fn() },
}));

jest.mock("shared/assets/images/registration.svg", () => ({
  __esModule: true,
  ReactComponent: () => null,
}));

const renderPage = () =>
  render(
    <CookiesProvider defaultSetOptions={{ path: "/" }}>
      <ChakraProvider>
        <MemoryRouter initialEntries={[ROUTE_PATHS.signUp]}>
          <Routes>
            <Route path={ROUTE_PATHS.signUp} element={<SignUpPage />} />
            <Route path={ROUTE_PATHS.home} element={<p>candidate home</p>} />
          </Routes>
        </MemoryRouter>
      </ChakraProvider>
    </CookiesProvider>,
  );

const email = () => screen.getByPlaceholderText("Почта");
const password = () => screen.getByPlaceholderText("Пароль");
const submit = () => screen.getByRole("button", { name: "Создать" });

// fireEvent wraps its own dispatch in act(); what escapes is the work the
// component schedules afterwards. Draining one microtask inside act() in a
// second step settles it, and keeps the Testing Library call out of the block -
// eslint-plugin-testing-library bans wrapping its utils in act directly.
const flush = () =>
  act(async () => {
    await Promise.resolve();
  });

const type = async (element, value) => {
  fireEvent.change(element, { target: { value } });
  await flush();
};

const clickSubmit = async () => {
  fireEvent.click(submit());
  await flush();
};

beforeEach(() => {
  UserService.signUp.mockReset();
  ["token", "role", "userType", "userId", "userName"].forEach((name) => Cookies.remove(name));
});

test("offers the account type as a choice, defaulting to candidate", () => {
  renderPage();

  expect(screen.getByText("Регистрация")).toBeInTheDocument();
  expect(screen.getByRole("radio", { name: "Кандидат" })).toBeChecked();
  expect(screen.getByRole("radio", { name: "Работодатель" })).not.toBeChecked();
});

test("sends the chosen account type, not a hard-coded default", async () => {
  UserService.signUp.mockResolvedValue({ data: { access_token: "access-token" } });
  renderPage();
  await type(email(), "boss@example.com");
  await type(password(), "secret123");
  fireEvent.click(screen.getByRole("radio", { name: "Работодатель" }));
  await flush();

  await clickSubmit();

  await waitFor(() => expect(UserService.signUp).toHaveBeenCalledTimes(1));
  expect(UserService.signUp).toHaveBeenCalledWith({
    email: "boss@example.com",
    password: "secret123",
    typeUser: "employer",
  });
});

test("defaults the account type to candidate for an untouched form", async () => {
  UserService.signUp.mockResolvedValue({ data: { access_token: "access-token" } });
  renderPage();
  await type(email(), "ada@example.com");
  await type(password(), "secret123");

  await clickSubmit();

  await waitFor(() => expect(UserService.signUp).toHaveBeenCalledTimes(1));
  expect(UserService.signUp).toHaveBeenCalledWith(
    expect.objectContaining({ typeUser: "candidate" }),
  );
});

test("stores the token and moves on after a successful registration", async () => {
  UserService.signUp.mockResolvedValue({ data: { access_token: "access-token" } });
  renderPage();
  await type(email(), "ada@example.com");
  await type(password(), "secret123");

  await clickSubmit();

  expect(await screen.findByText("candidate home")).toBeInTheDocument();
  expect(Cookies.get("token")).toBe("access-token");
});

test("rejects a password shorter than six characters", async () => {
  renderPage();
  await type(email(), "ada@example.com");
  await type(password(), "12345");

  await clickSubmit();

  expect(await screen.findByText("Пароль короче 6 символов")).toBeInTheDocument();
  expect(UserService.signUp).not.toHaveBeenCalled();
});

test("reports a refused registration without creating a session", async () => {
  UserService.signUp.mockRejectedValue({ response: { data: { detail: "Почта занята" } } });
  renderPage();
  await type(email(), "ada@example.com");
  await type(password(), "secret123");

  await clickSubmit();

  expect(await screen.findByText("Не удалось создать пользователя")).toBeInTheDocument();
  expect(screen.getByText("Почта занята")).toBeInTheDocument();
  expect(Cookies.get("token")).toBeUndefined();
});
