import * as sessionModule from "shared/auth/session";
import { sessionFromTokens } from "shared/auth/session";

// Builds a real token shaped like the backend's RS256 access token. The signature
// is fake on purpose: the browser never verifies it - decodeTokenPayload only
// reads the payload segment.
const tokenWith = (payload) => {
  const header = Buffer.from(JSON.stringify({ alg: "RS256", typ: "JWT" }), "utf8")
    .toString("base64url");
  const body = Buffer.from(JSON.stringify(payload), "utf8").toString("base64url");
  return `${header}.${body}.signature`;
};

// The payload shape backend/service/shared/tokens.py:95-103 signs: sub is the
// role, plus user_id and type.
const BACKEND_PAYLOAD = { sub: "candidate", user_id: 42, type: "candidate", access_token_type: "access" };

describe("sessionFromTokens", () => {
  test("maps the backend payload onto the cookie shape", () => {
    const session = sessionFromTokens({ access_token: tokenWith(BACKEND_PAYLOAD) });

    expect(session).toEqual({
      token: expect.any(String),
      role: "candidate",
      userType: "candidate",
      userId: 42,
      userName: undefined,
    });
  });

  test("takes the email it is handed as userName, because the token carries no email", () => {
    const session = sessionFromTokens(
      { access_token: tokenWith(BACKEND_PAYLOAD) },
      { email: "user@example.com" },
    );
    expect(session.userName).toBe("user@example.com");
  });

  test("returns the still-encoded token, which setUser then stores verbatim", () => {
    const accessToken = tokenWith(BACKEND_PAYLOAD);
    expect(sessionFromTokens({ access_token: accessToken }).token).toBe(accessToken);
  });

  test("accepts a token field as well as access_token", () => {
    // The token endpoint spells it access_token; a second shape exists so a
    // refresh response or a test fixture can reuse this helper.
    const token = tokenWith(BACKEND_PAYLOAD);
    expect(sessionFromTokens({ token }).token).toBe(token);
  });

  test("access_token wins over token when both are present", () => {
    const winner = tokenWith(BACKEND_PAYLOAD);
    const loser = tokenWith({ sub: "employer", user_id: 1, type: "employer" });

    expect(sessionFromTokens({ access_token: winner, token: loser }).token).toBe(winner);
  });

  test("with no tokens at all every field is undefined and nothing throws", () => {
    expect(sessionFromTokens(undefined)).toEqual({
      token: undefined,
      role: undefined,
      userType: undefined,
      userId: undefined,
      userName: undefined,
    });
    expect(sessionFromTokens({})).toEqual({
      token: undefined,
      role: undefined,
      userType: undefined,
      userId: undefined,
      userName: undefined,
    });
  });

  test("an unreadable token still yields the token itself, with no identity claims", () => {
    // A malformed payload must not throw here: the user is signed in as far as
    // the server is concerned, and the cookie is simply incomplete until the
    // next /users/ round trip fills it in.
    const session = sessionFromTokens({ access_token: "not-a-jwt" });

    expect(session.token).toBe("not-a-jwt");
    expect(session.role).toBeUndefined();
    expect(session.userId).toBeUndefined();
  });

  test("an employer token maps type and role independently", () => {
    // The two claims are separate: role is the permission (user|admin) and type
    // is the user kind (candidate|employer).
    const session = sessionFromTokens({
      access_token: tokenWith({ sub: "admin", user_id: 7, type: "employer" }),
    });
    expect(session.role).toBe("admin");
    expect(session.userType).toBe("employer");
    expect(session.userId).toBe(7);
  });

  test("user_id 0 survives, because the mapper copies rather than truthiness-tests", () => {
    const session = sessionFromTokens({
      access_token: tokenWith({ sub: "candidate", user_id: 0, type: "candidate" }),
    });
    expect(session.userId).toBe(0);
  });

  test("the module exposes a default export equal to the named one", () => {
    // Both spellings are imported somewhere in the tree; a second definition
    // would let them drift.
    expect(sessionModule.default).toBe(sessionFromTokens);
  });
});
