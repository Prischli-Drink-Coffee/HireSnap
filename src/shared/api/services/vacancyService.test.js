import { Instance } from "shared/api/instance";
import { getToken } from "shared/auth/userCookie";
import VacancyService from "shared/api/services/vacancyService";

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

const formEntries = (body) => Object.fromEntries(body.entries());
const AUTH = { headers: { Authorization: "Bearer unit-test-token" } };

beforeEach(() => {
  jest.clearAllMocks();
  getToken.mockReturnValue("Bearer unit-test-token");
});

describe("reads", () => {
  test("getAll GETs the collection", () => {
    VacancyService.getAll();
    expect(Instance.get).toHaveBeenCalledWith("vacancies/");
  });

  test("getById GETs vacancies/vacancy_id/{id}", () => {
    VacancyService.getById(3);
    expect(Instance.get).toHaveBeenCalledWith("vacancies/vacancy_id/3");
  });

  test("getRecommendedCandidates GETs the inference route with the token", () => {
    VacancyService.getRecommendedCandidates(3);
    expect(Instance.get).toHaveBeenCalledWith(
      "get_all_candidates/inference/vacancy/3",
      AUTH,
    );
  });
});

describe("update", () => {
  test("PUTs a JSON body to vacancies/{id} with the token", () => {
    const vacancy = { name: "Разработчик" };
    VacancyService.update(3, vacancy);

    const [path, body, config] = Instance.put.mock.calls[0];
    expect(path).toBe("vacancies/3");
    expect(body).toBe(vacancy);
    expect(body).not.toBeInstanceOf(FormData);
    expect(config).toEqual(AUTH);
  });
});

describe("createWithInference", () => {
  test("POSTs the multipart inference route with name and description", () => {
    VacancyService.createWithInference({
      name: "Разработчик",
      description: "Пишет сервисы на Python",
    });

    const [path, body, config] = Instance.post.mock.calls[0];
    expect(path).toBe("upload_vacancy/inference/employer");
    expect(body).toBeInstanceOf(FormData);
    expect(formEntries(body)).toEqual({
      name: "Разработчик",
      description: "Пишет сервисы на Python",
    });
    expect(config).toEqual(AUTH);
  });

  test("appends salary and skill only when they carry a value", () => {
    VacancyService.createWithInference({
      name: "Разработчик",
      description: "Пишет сервисы на Python",
      salary: "100000",
      skill: "FastAPI",
    });
    expect(formEntries(Instance.post.mock.calls[0][1])).toEqual({
      name: "Разработчик",
      description: "Пишет сервисы на Python",
      salary: "100000",
      skill: "FastAPI",
    });

    jest.clearAllMocks();
    VacancyService.createWithInference({
      name: "Разработчик",
      description: "Пишет сервисы на Python",
      salary: "",
      skill: undefined,
    });
    // An empty optional field must not be stored as a real blank value on the
    // server, which would then be indistinguishable from "not provided".
    expect(formEntries(Instance.post.mock.calls[0][1])).toEqual({
      name: "Разработчик",
      description: "Пишет сервисы на Python",
    });
  });

  test("the route this method posts to is the route the backend serves", () => {
    // backend/service/services/matching/presentation/routers/inference_api/
    // inference_api.py:65 declares POST "/upload_vacancy/inference/employer".
    // The handler behind it is still a stub (matching_service.py:343-353 returns
    // {"message": "test"} and persists nothing), which is why the form's toast
    // says "request sent" rather than "vacancy added" - but the PATH is correct
    // and must not drift to /vacancies/, which skips the embedding.
    expect(VacancyService.createWithInference.toString()).toContain(
      "createWithInference",
    );
    VacancyService.createWithInference({ name: "n", description: "d" });
    expect(Instance.post.mock.calls[0][0]).toBe(
      "upload_vacancy/inference/employer",
    );
    expect(Instance.post.mock.calls[0][0]).not.toBe("vacancies/");
  });
});
