import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ChakraProvider } from "@chakra-ui/react";
import EmployerPage from "pages/employer/EmployerPage";
import VacancyService from "shared/api/services/vacancyService";

// The page wires three existing endpoints together. What it owns is the
// selection state: which vacancy the candidate table is scoped to, and when the
// list is refetched. Both are pinned below.
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

beforeEach(() => {
  mockVacancyTableRenders.length = 0;
  mockCandidateTableProps.length = 0;
  VacancyService.createWithInference.mockReset();
  VacancyService.createWithInference.mockResolvedValue({ data: {} });
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
