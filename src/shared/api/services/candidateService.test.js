import { Instance } from "shared/api/instance";
import { getToken } from "shared/auth/userCookie";
import CandidateService from "shared/api/services/candidateService";

jest.mock("shared/api/instance", () => ({
  __esModule: true,
  Instance: {
    get: jest.fn(),
    post: jest.fn(),
    put: jest.fn(),
    delete: jest.fn(),
  },
}));

// CRA sets resetMocks:true (node_modules/react-scripts/scripts/utils/
// createJestConfig.js:68), so every mock is reset before each test - including
// this one. The token shape is therefore re-established in beforeEach.
jest.mock("shared/auth/userCookie", () => ({
  __esModule: true,
  getToken: jest.fn(),
}));

const AUTH = { headers: { Authorization: "Bearer unit-test-token" } };
const video = () => new File(["clip"], "clip.mp4", { type: "video/mp4" });
const keys = (body) => Object.keys(Object.fromEntries(body.entries()));

beforeEach(() => {
  jest.clearAllMocks();
  getToken.mockReturnValue("Bearer unit-test-token");
});

describe("uploadVideo", () => {
  test("POSTs upload_video/inference/{user_id} as multipart with the token", () => {
    const file = video();
    CandidateService.uploadVideo(42, file);

    const [path, body, config] = Instance.post.mock.calls[0];
    expect(path).toBe("upload_video/inference/42");
    expect(body).toBeInstanceOf(FormData);
    // The route declares `file: UploadFile` with no Form(...), so the field name
    // is part of the contract: "video" or "upload" would be a 422.
    expect(body.get("file")).toBe(file);
    expect(body.get("file").name).toBe("clip.mp4");
    expect(config).toEqual(AUTH);
  });

  test("adds the optional profile fields the route accepts", () => {
    CandidateService.uploadVideo(42, video(), {
      name: "Иван",
      phone: "+70000000000",
      date_birth: "1990-01-01",
      gender: "male",
    });

    const entries = Object.fromEntries(Instance.post.mock.calls[0][1].entries());
    expect(entries).toEqual({
      file: expect.any(File),
      name: "Иван",
      phone: "+70000000000",
      date_birth: "1990-01-01",
      gender: "male",
    });
  });

  test("skips undefined, null and empty-string details instead of sending blanks", () => {
    CandidateService.uploadVideo(42, video(), {
      name: "Иван",
      phone: "",
      gender: null,
      date_birth: undefined,
    });

    const body = Instance.post.mock.calls[0][1];
    expect(keys(body)).toEqual(["file", "name"]);
    // An empty phone must not reach the server as "" or as "undefined".
    expect(body.get("phone")).toBeNull();
    expect(body.get("gender")).toBeNull();
  });

  test("works with no details at all", () => {
    CandidateService.uploadVideo(42, video());
    expect(keys(Instance.post.mock.calls[0][1])).toEqual(["file"]);
  });
});

describe("getRecommendedVacancies", () => {
  test("GETs the candidate's inference route with the token", () => {
    CandidateService.getRecommendedVacancies(42);
    expect(Instance.get).toHaveBeenCalledWith(
      "get_all_vacancies/inference/candidate/42",
      AUTH,
    );
  });
});
