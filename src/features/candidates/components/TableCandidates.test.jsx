import React from "react";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import { ChakraProvider } from "@chakra-ui/react";
import TableCandidates from "features/candidates/components/TableCandidates";
import VacancyService from "shared/api/services/vacancyService";

// A render smoke test for the employer's candidate table. It pins what a reader
// depends on: the row shape the inference route returns ({id, user_id,
// vacancy_id, distance}), the honest empty state, and the fact that the table
// never asks the API for anything when no vacancy is selected.
jest.mock("shared/api/services/vacancyService", () => ({
  __esModule: true,
  default: { getRecommendedCandidates: jest.fn() },
}));

// The table fetches from an effect and stores the answer with setState. Awaiting
// a turn of the microtask queue inside act() lets that update land inside the
// test instead of after it, which is what React's act() warning is about.
const flush = () =>
  act(async () => {
    await Promise.resolve();
  });

const renderTable = async (props = {}) => {
  const view = render(
    <ChakraProvider>
      <TableCandidates {...props} />
    </ChakraProvider>,
  );
  await flush();
  return view;
};

const match = (id, userId, distance) => ({
  id,
  user_id: userId,
  vacancy_id: 3,
  distance,
});

beforeEach(() => {
  jest.clearAllMocks();
});

test("renders a row per match returned by the inference route", async () => {
  VacancyService.getRecommendedCandidates.mockResolvedValue({
    data: [match(1, 7, "0.824"), match(2, 9, "0.412")],
  });
  await renderTable({ vacancyId: 3 });

  expect(await screen.findByText("Кандидат #7")).toBeInTheDocument();
  expect(screen.getByText("Кандидат #9")).toBeInTheDocument();
  expect(VacancyService.getRecommendedCandidates).toHaveBeenCalledWith(3);
});

test("prints the similarity to three decimals, because that is all the order means", async () => {
  VacancyService.getRecommendedCandidates.mockResolvedValue({
    data: [match(1, 7, "0.8244")],
  });
  await renderTable({ vacancyId: 3 });

  expect(await screen.findByText("0.824")).toBeInTheDocument();
});

test("an absent score reads as a dash, not as zero similarity", async () => {
  // DECIMAL comes back as a string; a missing one must not be coerced to 0.000,
  // which is the string a genuinely dissimilar candidate prints.
  VacancyService.getRecommendedCandidates.mockResolvedValue({
    data: [match(1, 7, null)],
  });
  await renderTable({ vacancyId: 3 });

  const row = await screen.findByRole("row", { name: /Кандидат #7/ });
  expect(within(row).getByText("—")).toBeInTheDocument();
});

test("says so plainly when the vacancy has no matches yet", async () => {
  VacancyService.getRecommendedCandidates.mockResolvedValue({ data: [] });
  await renderTable({ vacancyId: 3 });

  expect(await screen.findByText("Совпадений пока нет.")).toBeInTheDocument();
});

test("treats a non-array body as empty instead of crashing", async () => {
  VacancyService.getRecommendedCandidates.mockResolvedValue({ data: null });
  await renderTable({ vacancyId: 3 });

  expect(await screen.findByText("Совпадений пока нет.")).toBeInTheDocument();
});

test("surfaces the error text the hook captured", async () => {
  VacancyService.getRecommendedCandidates.mockRejectedValue(
    new Error("Сервер недоступен"),
  );
  await renderTable({ vacancyId: 3 });

  expect(await screen.findByText("Сервер недоступен")).toBeInTheDocument();
});

test("asks for nothing until a vacancy is selected", async () => {
  await renderTable();

  expect(await screen.findByText("Совпадений пока нет.")).toBeInTheDocument();
  expect(VacancyService.getRecommendedCandidates).not.toHaveBeenCalled();
});

test("refetches when the employer picks another vacancy", async () => {
  VacancyService.getRecommendedCandidates.mockResolvedValue({
    data: [match(1, 7, "0.824")],
  });
  const { rerender } = await renderTable({ vacancyId: 3 });
  await screen.findByText("Кандидат #7");

  rerender(
    <ChakraProvider>
      <TableCandidates vacancyId={4} />
    </ChakraProvider>,
  );
  await flush();

  await waitFor(() =>
    expect(VacancyService.getRecommendedCandidates).toHaveBeenLastCalledWith(4),
  );
  expect(VacancyService.getRecommendedCandidates).toHaveBeenCalledTimes(2);
});
