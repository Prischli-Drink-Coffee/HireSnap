import React from "react";
import { act, render, screen, within } from "@testing-library/react";
import { ChakraProvider } from "@chakra-ui/react";
import TableMatchedVacancies from "features/candidates/components/TableMatchedVacancies";
import CandidateService from "shared/api/services/candidateService";
import VacancyService from "shared/api/services/vacancyService";

// The candidate's recommendation list is the one screen that joins two endpoints
// in the browser: match rows from the inference route, names from /vacancies/.
// The join, the ordering and the fallback name are therefore worth pinning - a
// silent mismatch here shows a candidate the wrong company's vacancy.
jest.mock("shared/api/services/candidateService", () => ({
  __esModule: true,
  default: { getRecommendedVacancies: jest.fn() },
}));

jest.mock("shared/api/services/vacancyService", () => ({
  __esModule: true,
  default: { getAll: jest.fn() },
}));

// Both requests settle inside an effect; awaiting a microtask turn inside act()
// lets the state update land inside the test rather than after it.
const flush = () =>
  act(async () => {
    await Promise.resolve();
  });

const renderTable = async (props = {}) => {
  const view = render(
    <ChakraProvider>
      <TableMatchedVacancies {...props} />
    </ChakraProvider>,
  );
  await flush();
  return view;
};

const match = (id, vacancyId, distance) => ({
  id,
  user_id: 7,
  vacancy_id: vacancyId,
  distance,
});

const vacancy = (id, name, salary = "100000") => ({ id, name, salary, skill: "Python" });

const bodyRows = () => screen.getAllByRole("row").slice(1);

beforeEach(() => {
  jest.clearAllMocks();
});

test("joins match rows with vacancy names from the collection", async () => {
  CandidateService.getRecommendedVacancies.mockResolvedValue({
    data: [match(1, 10, "0.5")],
  });
  VacancyService.getAll.mockResolvedValue({ data: [vacancy(10, "Разработчик")] });
  await renderTable({ userId: 7 });

  expect(await screen.findByText("Разработчик")).toBeInTheDocument();
  expect(CandidateService.getRecommendedVacancies).toHaveBeenCalledWith(7);
  expect(VacancyService.getAll).toHaveBeenCalledTimes(1);
});

test("orders the matches by similarity, best first", async () => {
  CandidateService.getRecommendedVacancies.mockResolvedValue({
    data: [match(1, 10, "0.2"), match(2, 11, "0.9"), match(3, 12, "0.55")],
  });
  VacancyService.getAll.mockResolvedValue({
    data: [vacancy(10, "Слабый"), vacancy(11, "Сильный"), vacancy(12, "Средний")],
  });
  await renderTable({ userId: 7 });

  await screen.findByText("Сильный");
  const names = bodyRows().map((row) => within(row).getAllByRole("cell")[0].textContent);
  expect(names).toEqual(["Сильный", "Средний", "Слабый"]);
});

test("compares similarities numerically, not as strings", async () => {
  // The column is DECIMAL, so axios hands back strings. Sorting them as text
  // would put "0.9" above "0.85" incorrectly - string order is "0.85" < "0.9"
  // only by luck; "0.10" vs "0.9" is where the two orders disagree.
  CandidateService.getRecommendedVacancies.mockResolvedValue({
    data: [match(1, 10, "0.10"), match(2, 11, "0.9")],
  });
  VacancyService.getAll.mockResolvedValue({
    data: [vacancy(10, "Десять"), vacancy(11, "Девять")],
  });
  await renderTable({ userId: 7 });

  await screen.findByText("Девять");
  const names = bodyRows().map((row) => within(row).getAllByRole("cell")[0].textContent);
  expect(names).toEqual(["Девять", "Десять"]);
});

test("falls back to the id when a match points at an unknown vacancy", async () => {
  CandidateService.getRecommendedVacancies.mockResolvedValue({
    data: [match(1, 99, "0.5")],
  });
  VacancyService.getAll.mockResolvedValue({ data: [vacancy(10, "Разработчик")] });
  await renderTable({ userId: 7 });

  expect(await screen.findByText("Вакансия #99")).toBeInTheDocument();
});

test("shows an em dash for a vacancy with no salary", async () => {
  CandidateService.getRecommendedVacancies.mockResolvedValue({
    data: [match(1, 10, "0.5")],
  });
  VacancyService.getAll.mockResolvedValue({ data: [vacancy(10, "Стажёр", null)] });
  await renderTable({ userId: 7 });

  await screen.findByText("Стажёр");
  expect(within(bodyRows()[0]).getByText("—")).toBeInTheDocument();
});

test("invites an upload when the candidate has no recommendations", async () => {
  CandidateService.getRecommendedVacancies.mockResolvedValue({ data: [] });
  VacancyService.getAll.mockResolvedValue({ data: [] });
  await renderTable({ userId: 7 });

  expect(
    await screen.findByText(
      "Рекомендаций пока нет — загрузите видео, чтобы получить подборку.",
    ),
  ).toBeInTheDocument();
});

test("treats non-array bodies as empty instead of crashing", async () => {
  CandidateService.getRecommendedVacancies.mockResolvedValue({ data: null });
  VacancyService.getAll.mockResolvedValue({ data: undefined });
  await renderTable({ userId: 7 });

  expect(
    await screen.findByText(
      "Рекомендаций пока нет — загрузите видео, чтобы получить подборку.",
    ),
  ).toBeInTheDocument();
});

test("surfaces the error text when either request fails", async () => {
  CandidateService.getRecommendedVacancies.mockRejectedValue(
    new Error("Сервер недоступен"),
  );
  VacancyService.getAll.mockResolvedValue({ data: [] });
  await renderTable({ userId: 7 });

  expect(await screen.findByText("Сервер недоступен")).toBeInTheDocument();
});

test("asks for nothing until a user is known", async () => {
  await renderTable();

  expect(
    await screen.findByText(
      "Рекомендаций пока нет — загрузите видео, чтобы получить подборку.",
    ),
  ).toBeInTheDocument();
  expect(CandidateService.getRecommendedVacancies).not.toHaveBeenCalled();
  expect(VacancyService.getAll).not.toHaveBeenCalled();
});
