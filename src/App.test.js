import { render, screen } from "@testing-library/react";
import App from "App";
import AppProviders from "app/providers";

// A smoke test, not a behavioural one: it proves the provider tree + router +
// the unauthenticated branch all mount without throwing. An anonymous visit to
// "/" is redirected by PrivateRoutes to /sign_in, so the sign-in heading is the
// expected end state - and it needs no cookie and no network call.
//
// Anything that fetches (the candidate page, the employer page) is intentionally
// out of scope here: a unit test must not depend on a live backend.
test("renders the sign-in screen for an anonymous visitor", () => {
  render(
    <AppProviders>
      <App />
    </AppProviders>,
  );

  expect(screen.getByText("Вход в систему")).toBeInTheDocument();
});
