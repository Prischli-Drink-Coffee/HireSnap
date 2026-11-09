import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ChakraProvider } from "@chakra-ui/react";
import VacancyCreateForm from "features/vacancies/components/VacancyCreateForm";
import VacancyService from "shared/api/services/vacancyService";

// The employer's create form. Two things matter and nothing else does: the
// service receives the values the user typed, and the success toast tells the
// truth about a route that persists nothing yet.
jest.mock("shared/api/services/vacancyService", () => ({
  __esModule: true,
  default: { createWithInference: jest.fn() },
}));

const flush = () =>
  act(async () => {
    await Promise.resolve();
  });

const renderForm = (props = {}) =>
  render(
    <ChakraProvider>
      <VacancyCreateForm {...props} />
    </ChakraProvider>,
  );

const field = (label) => screen.getByPlaceholderText(label);
const submit = () => screen.getByRole("button", { name: "Добавить вакансию" });

const fill = ({ name, description, salary, skill }) => {
  if (name !== undefined) fireEvent.change(field("Название вакансии"), { target: { value: name } });
  if (description !== undefined) {
    fireEvent.change(field("Описание"), { target: { value: description } });
  }
  if (salary !== undefined) fireEvent.change(field("Зарплата"), { target: { value: salary } });
  if (skill !== undefined) fireEvent.change(field("Навыки"), { target: { value: skill } });
};

const VALID = {
  name: "Senior Python developer",
  description: "Asynchronous services, MySQL and a lot of tests.",
};

beforeEach(() => {
  jest.clearAllMocks();
});

test("renders the four fields and a submit button", () => {
  renderForm();

  expect(field("Название вакансии")).toBeInTheDocument();
  expect(field("Описание")).toBeInTheDocument();
  expect(field("Зарплата")).toBeInTheDocument();
  expect(field("Навыки")).toBeInTheDocument();
  expect(submit()).toBeInTheDocument();
});

test("posts the typed values through the service, once", async () => {
  VacancyService.createWithInference.mockResolvedValue({ data: { message: "test" } });
  renderForm();
  fill({ ...VALID, salary: "250000", skill: "FastAPI, MySQL" });

  fireEvent.click(submit());

  await waitFor(() => expect(VacancyService.createWithInference).toHaveBeenCalledTimes(1));
  expect(VacancyService.createWithInference).toHaveBeenCalledWith({
    name: "Senior Python developer",
    description: "Asynchronous services, MySQL and a lot of tests.",
    salary: "250000",
    skill: "FastAPI, MySQL",
  });
});

test("sends empty optional strings, not undefined, so the multipart body keeps every field", () => {
  // Formik initialises salary and skill to "", and the service appends only
  // truthy values - so an untouched form still sends name and description and
  // omits the two optionals. This pins the pair it does send.
  VacancyService.createWithInference.mockResolvedValue({ data: {} });
  renderForm();
  fill(VALID);

  fireEvent.click(submit());

  return waitFor(() =>
    expect(VacancyService.createWithInference).toHaveBeenCalledWith(
      expect.objectContaining({ name: VALID.name, description: VALID.description }),
    ),
  );
});

test("says the request was sent, not that the vacancy was created", async () => {
  // The backend route is a stub (matching_service.upload_vacancy returns
  // {"message": "test"}), so the row never appears below. The toast must not
  // claim otherwise.
  VacancyService.createWithInference.mockResolvedValue({ data: { message: "test" } });
  renderForm();
  fill(VALID);

  fireEvent.click(submit());
  await flush();

  expect(await screen.findByText("Запрос отправлен")).toBeInTheDocument();
  expect(screen.queryByText(/Вакансия добавлена/)).toBeNull();
});

test("calls onCreated and clears the form after a successful submit", async () => {
  VacancyService.createWithInference.mockResolvedValue({ data: {} });
  const onCreated = jest.fn();
  renderForm({ onCreated });
  fill(VALID);
  fireEvent.click(submit());

  await waitFor(() => expect(onCreated).toHaveBeenCalledTimes(1));
  // resetForm() clears the values, but the assertion has to wait for the commit
  // that follows it, or it reads the pre-reset input.
  await waitFor(() => expect(field("Название вакансии")).toHaveValue(""));
  expect(field("Описание")).toHaveValue("");
});

test("reports a rejected request and keeps the typed values for a retry", async () => {
  VacancyService.createWithInference.mockRejectedValue(new Error("Сервер недоступен"));
  renderForm();
  fill(VALID);
  fireEvent.click(submit());

  expect(await screen.findByText("Не удалось добавить вакансию")).toBeInTheDocument();
  expect(field("Название вакансии")).toHaveValue(VALID.name);
});

test("does not submit an empty name or a too-short description", async () => {
  renderForm();
  fill({ name: "", description: "short" });

  fireEvent.click(submit());
  await flush();

  expect(await screen.findByText("Укажите название")).toBeInTheDocument();
  expect(await screen.findByText("Слишком короткое описание")).toBeInTheDocument();
  expect(VacancyService.createWithInference).not.toHaveBeenCalled();
});

test("rejects a description over the 2000-character ceiling", async () => {
  renderForm();
  fill({ ...VALID, description: "x".repeat(2001) });

  fireEvent.click(submit());
  await flush();

  expect(await screen.findByText("Слишком длинное описание")).toBeInTheDocument();
  expect(VacancyService.createWithInference).not.toHaveBeenCalled();
});

test("does not call the service twice when the button is clicked twice quickly", async () => {
  let release;
  VacancyService.createWithInference.mockReturnValue(
    new Promise((resolve) => {
      release = () => resolve({ data: {} });
    }),
  );
  renderForm();
  fill(VALID);

  // Grab the button once: while isSubmitting is true Chakra replaces its label
  // with a spinner and loadingText, so re-querying by name mid-flight fails.
  const button = submit();
  fireEvent.click(button);
  await flush();
  fireEvent.click(button);
  await flush();

  expect(VacancyService.createWithInference).toHaveBeenCalledTimes(1);
  await act(async () => {
    release();
  });
});
