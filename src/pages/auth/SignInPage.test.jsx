import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ChakraProvider } from "@chakra-ui/react";
import { CookiesProvider } from "react-cookie";
import Cookies from "js-cookie";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import SignInPage from "pages/auth/SignInPage";
import UserService from "shared/api/services/userService";
import { ROUTE_PATHS } from "constants.js";

// Sign-in is where the cookie is born, so the assertions are: the credentials go
// out as the service expects, a success fills the cookie and leaves the page, and
// a failure keeps the user on it with an honest message.
jest.mock("shared/api/services/userService", () => ({
  __esModule: true,
  default: { signIn: jest.fn() },
}));

// CRA maps .svg to a string stub in Jest, so the ReactComponent import the page
// uses for its background is undefined there. A local stub keeps the assertion
// about behaviour instead of about the bundler.
jest.mock("shared/assets/images/authorization.svg", () => ({
  __esModule: true,
  ReactComponent: () => null,
}));

const renderPage = () =>
  render(
    <CookiesProvider defaultSetOptions={{ path: "/" }}>
      <ChakraProvider>
        <MemoryRouter initialEntries={[ROUTE_PATHS.signIn]}>
          <Routes>
            <Route path={ROUTE_PATHS.signIn} element={<SignInPage />} />
            <Route path={ROUTE_PATHS.home} element={<p>candidate home</p>} />
          </Routes>
        </MemoryRouter>
      </ChakraProvider>
    </CookiesProvider>,
  );

const email = () => screen.getByPlaceholderText("Почта");
const password = () => screen.getByPlaceholderText("Пароль");
const submit = () => screen.getByRole("button", { name: "Войти" });

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
  UserService.signIn.mockReset();
  ["token", "role", "userType", "userId", "userName"].forEach((name) => Cookies.remove(name));
});

test("renders the two fields and the submit button", () => {
  renderPage();

  expect(screen.getByText("Вход в систему")).toBeInTheDocument();
  expect(email()).toBeInTheDocument();
  expect(password()).toBeInTheDocument();
  expect(submit()).toBeEnabled();
});

test("sends the typed credentials, once", async () => {
  UserService.signIn.mockResolvedValue({
    data: { access_token: "access-token", refresh_token: "refresh-token" },
  });
  renderPage();
  await type(email(), "ada@example.com");
  await type(password(), "secret123");

  await clickSubmit();

  await waitFor(() => expect(UserService.signIn).toHaveBeenCalledTimes(1));
  expect(UserService.signIn).toHaveBeenCalledWith("ada@example.com", "secret123");
});

test("stores the token and moves to the home route on success", async () => {
  UserService.signIn.mockResolvedValue({
    data: { access_token: "access-token", refresh_token: "refresh-token" },
  });
  renderPage();
  await type(email(), "ada@example.com");
  await type(password(), "secret123");

  await clickSubmit();

  expect(await screen.findByText("candidate home")).toBeInTheDocument();
  expect(Cookies.get("token")).toBe("access-token");
  expect(Cookies.get("userName")).toBe("ada@example.com");
});

test("rejects an empty form without calling the service", async () => {
  renderPage();

  await clickSubmit();

  expect(await screen.findByText("Укажите почту")).toBeInTheDocument();
  expect(screen.getByText("Укажите пароль")).toBeInTheDocument();
  expect(UserService.signIn).not.toHaveBeenCalled();
});

test("rejects an address that is not an address", async () => {
  renderPage();
  await type(email(), "ada.example.com");
  await type(password(), "secret123");

  await clickSubmit();

  expect(await screen.findByText("Некорректный адрес почты")).toBeInTheDocument();
  expect(UserService.signIn).not.toHaveBeenCalled();
});

test("stays on the page and shows the server's message when the credentials are refused", async () => {
  UserService.signIn.mockRejectedValue({ response: { data: { detail: "Неверный пароль" } } });
  renderPage();
  await type(email(), "ada@example.com");
  await type(password(), "wrong");

  await clickSubmit();

  expect(await screen.findByText("Не удалось войти")).toBeInTheDocument();
  expect(screen.getByText("Неверный пароль")).toBeInTheDocument();
  expect(screen.queryByText("candidate home")).toBeNull();
  expect(Cookies.get("token")).toBeUndefined();
});

test("falls back to a generic message when the failure carries no detail", async () => {
  UserService.signIn.mockRejectedValue(new Error("Network Error"));
  renderPage();
  await type(email(), "ada@example.com");
  await type(password(), "secret123");

  await clickSubmit();

  expect(await screen.findByText("Проверьте почту и пароль")).toBeInTheDocument();
});
