import { decodeTokenPayload } from "shared/auth/decodeToken";

// Builds a real unsigned JWT: three dot-separated base64url segments. The
// signature is a placeholder because the browser never verifies it - it only
// reads the payload, and the server re-checks every request.
const makeToken = (payload, { encode = (json) => Buffer.from(json, "utf8").toString("base64url") } = {}) => {
  const header = encode(JSON.stringify({ alg: "RS256", typ: "JWT" }));
  const body = encode(JSON.stringify(payload));
  return `${header}.${body}.signature-not-verified`;
};

const PAYLOAD = {
  sub: "admin",
  user_id: 7,
  type: "candidate",
  access_token_type: "access_token_type",
};

describe("decodeTokenPayload", () => {
  test("returns the payload of a well-formed token", () => {
    expect(decodeTokenPayload(makeToken(PAYLOAD))).toEqual(PAYLOAD);
  });

  test("decodes the base64url alphabet, not just plain base64", () => {
    // A payload carrying an emoji encodes with "-" and "_"; the module rewrites
    // both to "+" and "/" before atob, and a token without them would never
    // exercise that branch.
    const token = makeToken({ ...PAYLOAD, note: "🎯~?" });
    expect(token.split(".")[1]).toMatch(/[-_]/);
    expect(decodeTokenPayload(token)).toEqual({ ...PAYLOAD, note: "🎯~?" });
  });

  test("pads a segment whose length is not a multiple of four", () => {
    // base64url drops "=" padding, and atob rejects an unpadded input whose
    // length is not a multiple of 4 - the module pads it back. The payload is
    // chosen so its encoding is 78 characters long, i.e. it needs two "=".
    const payload = { sub: "user", user_id: 42, type: "employer", note: "~~~" };
    const token = makeToken(payload);

    expect(token.split(".")[1].length % 4).toBe(2);
    expect(token).not.toContain("=");
    expect(decodeTokenPayload(token)).toEqual(payload);
  });

  // The three cases below are the contract, not trivia: the function runs during
  // render, so returning null is what lets a caller redirect to the sign-in page
  // instead of unmounting the tree with an exception.
  test("returns null for a token with no payload segment", () => {
    expect(decodeTokenPayload("not-a-jwt")).toBeNull();
  });

  test("returns null for a token whose payload segment is empty", () => {
    expect(decodeTokenPayload("header..signature")).toBeNull();
  });

  test("returns null when the payload is not JSON", () => {
    const garbage = Buffer.from("this is not json", "utf8").toString("base64url");
    expect(decodeTokenPayload(`header.${garbage}.signature`)).toBeNull();
  });

  test("returns null instead of throwing for input that is not a string", () => {
    expect(() => decodeTokenPayload(undefined)).not.toThrow();
    expect(decodeTokenPayload(undefined)).toBeNull();
    expect(decodeTokenPayload(null)).toBeNull();
    expect(decodeTokenPayload(42)).toBeNull();
    expect(decodeTokenPayload({})).toBeNull();
  });

  test("returns null for the empty string", () => {
    expect(decodeTokenPayload("")).toBeNull();
  });
});
