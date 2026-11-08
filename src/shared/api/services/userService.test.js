import { Instance } from "shared/api/instance";
import { getToken } from "shared/auth/userCookie";
import UserService from "shared/api/services/userService";

// Method, path and payload shape - the contract, not the network.
//
// A wrong path here is a runtime 404 that no other test in the tree can see, and
// a JSON body sent to a route that declares fastapi.Form(...) parameters is a 422
// that looks like a server-side validation bug. Both are pinned below.
jest.mock("shared/api/instance", () => ({
  __esModule: true,
  Instance: {
    get: jest.fn(),
    post: jest.fn(),
    put: jest.fn(),
    delete: jest.fn(),
  },
}));

// The factory is re-evaluated per module registry, and CRA sets resetMocks:true
// (node_modules/react-scripts/scripts/utils/createJestConfig.js:68), which wipes a
// mock's implementation before every test. So the real token shape is restored in
// beforeEach instead of relying on the factory's initial return value.
jest.mock("shared/auth/userCookie", () => ({
  __esModule: true,
  getToken: jest.fn(),
}));

// FormData is opaque in error output, so the payload is read back as entries.
const formEntries = (body) => Object.fromEntries(body.entries());

beforeEach(() => {
  jest.clearAllMocks();
  getToken.mockReturnValue("Bearer unit-test-token");
});

describe("signIn", () => {
  test("POSTs signin/ as a form body, not JSON", () => {
    UserService.signIn("user@example.com", "secret");

    expect(Instance.post).toHaveBeenCalledTimes(1);
    const [path, body, config] = Instance.post.mock.calls[0];
    expect(path).toBe("signin/");
    expect(body).toBeInstanceOf(FormData);
    expect(formEntries(body)).toEqual({
      email: "user@example.com",
      password: "secret",
    });
    // No third argument: axios must generate the multipart boundary itself, and a
    // hand-set Content-Type is exactly what breaks a form upload.
    expect(config).toBeUndefined();
  });
});

describe("signUp", () => {
  test("POSTs signup/ and maps typeUser onto the server's type_user field", () => {
    UserService.signUp({
      email: "new@example.com",
      password: "secret",
      typeUser: "employer",
      role: "user",
    });

    const [path, body] = Instance.post.mock.calls[0];
    expect(path).toBe("signup/");
    expect(body).toBeInstanceOf(FormData);
    expect(formEntries(body)).toEqual({
      email: "new@example.com",
      password: "secret",
      type_user: "employer",
      role: "user",
    });
    // The camelCase spelling would arrive as an unexpected field and the required
    // type_user Form(...) would come back missing.
    expect(body.get("typeUser")).toBeNull();
  });

  test("defaults to candidate/user, the pair the backend accepts", () => {
    UserService.signUp({ email: "new@example.com", password: "secret" });

    expect(formEntries(Instance.post.mock.calls[0][1])).toEqual({
      email: "new@example.com",
      password: "secret",
      type_user: "candidate",
      role: "user",
    });
  });
});

describe("reads", () => {
  test("getAll GETs the collection with no config", () => {
    UserService.getAll();
    expect(Instance.get).toHaveBeenCalledWith("users/");
  });

  test("getById GETs users/user_id/{id}", () => {
    UserService.getById(7);
    expect(Instance.get).toHaveBeenCalledWith("users/user_id/7");
  });

  test("getByEmail percent-encodes the address", () => {
    UserService.getByEmail("a+b@example.com");
    expect(Instance.get).toHaveBeenCalledWith("users/email/a%2Bb%40example.com");
  });
});

describe("update", () => {
  test("PUTs a JSON body to users/{id} with the bearer token attached", () => {
    const user = { email: "user@example.com", role: "admin" };
    UserService.update(7, user);

    const [path, body, config] = Instance.put.mock.calls[0];
    expect(path).toBe("users/7");
    expect(body).toBe(user);
    expect(body).not.toBeInstanceOf(FormData);
    expect(config).toEqual({
      headers: { Authorization: "Bearer unit-test-token" },
    });
  });

  test("reads the token at call time, not at import time", () => {
    // A token captured once at module load would still be the anonymous value
    // after sign-in, and every authenticated call would 401.
    getToken.mockReturnValueOnce("Bearer fresh-token");
    UserService.update(7, {});

    expect(Instance.put.mock.calls[0][2].headers.Authorization).toBe(
      "Bearer fresh-token",
    );
  });
});
