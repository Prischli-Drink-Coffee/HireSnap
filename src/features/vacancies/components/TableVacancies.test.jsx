import React from "react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { ChakraProvider } from "@chakra-ui/react";
import TableVacancies from "features/vacancies/components/TableVacancies";
import VacancyService from "shared/api/services/vacancyService";
import { PAGINATION } from "constants.js";

// A render smoke test for the employer's vacancy table: one mocked service call,
// no network, no router. It pins the three things a reader of this table depends
// on - the rows render, the empty state is honest, and the client-side paging
// really slices the array it was given.
jest.mock("shared/api/services/vacancyService", () => ({
  __esModule: true,
  default: { getAll: jest.fn() },
}));

const rows = (count) =>
  Array.from({ length: count }, (_, index) => ({
    id: index + 1,
    name: `Вакансия ${String(index + 1).padStart(2, "0")}`,
    salary: "100000",
    skill: "Python",
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
      <TableVacancies {...props} />
    </ChakraProvider>,
  );
  await flush();
  return view;
};

beforeEach(() => {
  jest.clearAllMocks();
});

test("renders a row per vacancy returned by GET /vacancies/", async () => {
  VacancyService.getAll.mockResolvedValue({ data: rows(3) });
  await renderTable();

  expect(await screen.findByText("Вакансия 01")).toBeInTheDocument();
  expect(screen.getByText("Вакансия 03")).toBeInTheDocument();
  expect(VacancyService.getAll).toHaveBeenCalledTimes(1);
});

test("says so plainly when the collection is empty", async () => {
  VacancyService.getAll.mockResolvedValue({ data: [] });
  await renderTable();

  expect(await screen.findByText("Вакансий пока нет.")).toBeInTheDocument();
});

test("treats a non-array body as empty instead of crashing", async () => {
  // A 200 with an unexpected shape (an error object, a null) must not take the
  // page down; the table is the only thing the employer sees.
  VacancyService.getAll.mockResolvedValue({ data: null });
  await renderTable();

  expect(await screen.findByText("Вакансий пока нет.")).toBeInTheDocument();
});

test("surfaces the error text the hook captured", async () => {
  VacancyService.getAll.mockRejectedValue(new Error("Сервер недоступен"));
  await renderTable();

  expect(await screen.findByText("Сервер недоступен")).toBeInTheDocument();
});

test("fetches once per mount, which is why the page remounts it with a key", async () => {
  VacancyService.getAll.mockResolvedValue({ data: rows(2) });
  const { rerender } = await renderTable();

  await screen.findByText("Вакансия 01");
  rerender(
    <ChakraProvider>
      <TableVacancies onSelectVacancy={jest.fn()} />
    </ChakraProvider>,
  );
  await flush();

  await waitFor(() => expect(VacancyService.getAll).toHaveBeenCalledTimes(1));
});

test("sorts by name ascending, and the header toggle reverses it", async () => {
  VacancyService.getAll.mockResolvedValue({ data: rows(3) });
  await renderTable();

  await screen.findByText("Вакансия 01");
  const bodyRowNames = () =>
    screen.getAllByRole("row").slice(1).map((row) => within(row).getAllByRole("cell")[1].textContent);

  expect(bodyRowNames()).toEqual(["Вакансия 01", "Вакансия 02", "Вакансия 03"]);

  // The sort control is the only image in the header row; the pagination chevrons
  // live below the table, so scoping to the first row picks the right one.
  const headerRow = screen.getAllByRole("row")[0];
  fireEvent.click(within(headerRow).getByRole("img", { hidden: true }));

  expect(bodyRowNames()).toEqual(["Вакансия 03", "Вакансия 02", "Вакансия 01"]);
});

test("shows one page at a time, not the whole collection", async () => {
  // The server cannot page, so this slicing is the only paging that exists.
  VacancyService.getAll.mockResolvedValue({ data: rows(PAGINATION.defaultPageSize + 2) });
  await renderTable();

  await screen.findByText("Вакансия 01");

  expect(screen.getByText("Вакансия 10")).toBeInTheDocument();
  expect(screen.queryByText("Вакансия 11")).toBeNull();
  expect(screen.getByText(/из 12/)).toBeInTheDocument();
});

test("calls onSelectVacancy with the whole row when its action is used", async () => {
  VacancyService.getAll.mockResolvedValue({ data: rows(1) });
  const onSelectVacancy = jest.fn();
  await renderTable({ onSelectVacancy });

  await screen.findByText("Вакансия 01");
  fireEvent.click(screen.getByText("Кандидаты"));

  expect(onSelectVacancy).toHaveBeenCalledWith(
    expect.objectContaining({ id: 1, name: "Вакансия 01" }),
  );
});

test("renders without an onSelectVacancy handler", async () => {
  // The prop is optional in the component; a click must not throw when the page
  // has not wired it.
  VacancyService.getAll.mockResolvedValue({ data: rows(1) });
  await renderTable();

  await screen.findByText("Вакансия 01");
  expect(() => fireEvent.click(screen.getByText("Кандидаты"))).not.toThrow();
});

test("shows an em dash for a vacancy with no salary or skill", async () => {
  VacancyService.getAll.mockResolvedValue({
    data: [{ id: 9, name: "Стажёр", salary: null, skill: "" }],
  });
  await renderTable();

  const row = await screen.findByRole("row", { name: /Стажёр/ });
  expect(within(row).getAllByText("—")).toHaveLength(2);
});
