import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ChakraProvider } from "@chakra-ui/react";
import EmployerPage from "pages/employer/EmployerPage";
import VacancyService from "shared/api/services/vacancyService";

// The page wires three existing endpoints together. What it owns is the
// selection state (which vacancy the candidate table is scoped to), when the
// list is refetched, and which created row it names back to the reader.
jest.mock("shared/api/services/vacancyService", () => ({
  __esModule: true,
  default: { createWithInference: jest.fn(), getAll: jest.fn() },
}));

// TableVacancies is replaced by a probe: it renders a control that selects a
// vacancy, and it counts its own mounts so the reload key is observable.
const mockVacancyTableRenders = [];
jest.mock("features/vacancies", () => {
  const React = require("react");
  const actual = jest.requireActual("features/vacancies");

  const TableVacanciesProbe = (props) => {
    // Count MOUNTS, not renders: the page re-renders on every selection change,
    // and only a key change unmounts this component.
    React.useEffect(() => {
      mockVacancyTableRenders.push(props);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    return React.createElement(
      "button",
      { type: "button", onClick: () => props.onSelectVacancy({ id: 11, name: "Backend" }) },
      "pick vacancy",
    );
  };
  TableVacanciesProbe.displayName = "TableVacanciesProbe";

  return { ...actual, TableVacancies: TableVacanciesProbe };
});

// The candidate table makes two requests and needs a vacancy id; a probe records
// the id the page passes down.
const mockCandidateTableProps = [];
jest.mock("features/candidates", () => {
  const React = require("react");
  const actual = jest.requireActual("features/candidates");

  const TableCandidatesProbe = (props) => {
    mockCandidateTableProps.push(props);
    return React.createElement("div", null, "candidates table");
  };
  TableCandidatesProbe.displayName = "TableCandidatesProbe";

  return { ...actual, TableCandidates: TableCandidatesProbe };
});

const renderPage = () =>
  render(
    <ChakraProvider>
      <EmployerPage />
    </ChakraProvider>,
  );

const field = (label) => screen.getByPlaceholderText(label);

// fireEvent wraps its own dispatch in act(); Formik validates in a promise, so
// the setState that follows is what escapes and lands on the NEXT test. Draining
// one microtask inside act() in a second step keeps each test self-contained
// without wrapping the Testing Library call itself.
const flush = () =>
  act(async () => {
    await Promise.resolve();
  });

const type = async (label, value) => {
  fireEvent.change(field(label), { target: { value } });
  await flush();
};

const submitCreate = async () => {
  fireEvent.click(screen.getByRole("button", { name: "Добавить вакансию" }));
  await flush();
};

// What the create route answers after it persists: the created row, exactly the
// six keys the backend documents.
const CREATED = {
  id: 42,
  name: "Аналитик",
  salary: "180000",
  description: "Собирать требования и проверять гипотезы.",
  skill: "SQL",
  embedding_id: 7,
};

beforeEach(() => {
  mockVacancyTableRenders.length = 0;
  mockCandidateTableProps.length = 0;
  VacancyService.createWithInference.mockReset();
  VacancyService.createWithInference.mockResolvedValue({ data: CREATED });
});

test("renders the three sections and an empty candidate panel", () => {
  renderPage();

  expect(screen.getByRole("heading", { name: "Вакансии" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Все вакансии" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Кандидаты" })).toBeInTheDocument();
  expect(
    screen.getByText("Выберите вакансию в таблице, чтобы увидеть подходящих кандидатов."),
  ).toBeInTheDocument();
  expect(screen.queryByText("candidates table")).toBeNull();
});

test("scopes the candidate table to the vacancy the reader picked", () => {
  renderPage();

  fireEvent.click(screen.getByRole("button", { name: "pick vacancy" }));

  expect(
    screen.getByRole("heading", { name: "Кандидаты на вакансию «Backend»" }),
  ).toBeInTheDocument();
  expect(mockCandidateTableProps).toHaveLength(1);
  expect(mockCandidateTableProps[0].vacancyId).toBe(11);
});

test("refetches the vacancy list after a create, by remounting the table", async () => {
  renderPage();
  expect(mockVacancyTableRenders).toHaveLength(1);

  await type("Название вакансии", "Аналитик");
  await type("Описание", "Собирать требования и проверять гипотезы.");
  await submitCreate();

  await waitFor(() => expect(mockVacancyTableRenders).toHaveLength(2));
  expect(VacancyService.createWithInference).toHaveBeenCalledTimes(1);
});

test("names the created vacancy and its id once the server has stored it", async () => {
  // A remount alone shows a page that looks unchanged: the new row is somewhere
  // in a table the reader has to re-read. Naming it, with the id the response
  // carried, is what proves the create landed.
  renderPage();
  expect(screen.queryByText(/сохранена/)).toBeNull();

  await type("Название вакансии", "Аналитик");
  await type("Описание", "Собирать требования и проверять гипотезы.");
  await submitCreate();

  expect(
    await screen.findByText("Вакансия «Аналитик» сохранена (id 42)."),
  ).toBeInTheDocument();
});

test("does not claim a saved row when the create was refused", async () => {
  VacancyService.createWithInference.mockReset();
  VacancyService.createWithInference.mockRejectedValue({
    message: "Request failed with status code 409",
    response: { status: 409, data: { detail: "Такая вакансия уже есть" } },
  });
  renderPage();
  expect(mockVacancyTableRenders).toHaveLength(1);

  await type("Название вакансии", "Аналитик");
  await type("Описание", "Собирать требования и проверять гипотезы.");
  await submitCreate();

  // The server's own explanation is what the reader gets; the list is not
  // remounted for a row that does not exist, and no confirmation appears.
  expect(await screen.findByText("Такая вакансия уже есть")).toBeInTheDocument();
  expect(screen.queryByText(/сохранена/)).toBeNull();
  expect(mockVacancyTableRenders).toHaveLength(1);
});

test("survives a create that fails with no response at all", async () => {
  VacancyService.createWithInference.mockReset();
  VacancyService.createWithInference.mockRejectedValue({
    message: "Network Error",
    response: undefined,
  });
  renderPage();

  await type("Название вакансии", "Аналитик");
  await type("Описание", "Собирать требования и проверять гипотезы.");
  await submitCreate();

  expect(await screen.findByText("Network Error")).toBeInTheDocument();
  expect(screen.queryByText(/сохранена/)).toBeNull();
  // The page is still usable: the form and the list are both still mounted.
  expect(screen.getByRole("button", { name: "Добавить вакансию" })).toBeInTheDocument();
  expect(mockVacancyTableRenders).toHaveLength(1);
});

test("keeps the previous selection visible while the list reloads", async () => {
  // The reload key remounts TableVacancies but not the selection: dropping it
  // would close the candidate panel under the reader after every create.
  renderPage();
  fireEvent.click(screen.getByRole("button", { name: "pick vacancy" }));

  await type("Название вакансии", "Аналитик");
  await type("Описание", "Собирать требования и проверять гипотезы.");
  await submitCreate();

  await waitFor(() => expect(mockVacancyTableRenders).toHaveLength(2));
  expect(
    screen.getByRole("heading", { name: "Кандидаты на вакансию «Backend»" }),
  ).toBeInTheDocument();
});
