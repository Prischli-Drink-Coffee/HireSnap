import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ChakraProvider } from "@chakra-ui/react";
import VacancyCreateForm from "features/vacancies/components/VacancyCreateForm";
import VacancyService from "shared/api/services/vacancyService";

// The employer's create form. Three things matter and nothing else does: the
// service receives the values the user typed, the created vacancy reaches the
// caller, and the toast reports what the server actually did.
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

// What the route answers after it persists: the created row, exactly the six
// keys the backend documents.
const CREATED = {
  id: 42,
  name: "Senior Python developer",
  salary: "250000",
  description: "Asynchronous services, MySQL and a lot of tests.",
  skill: "FastAPI, MySQL",
  embedding_id: 7,
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

test("says the vacancy was added, and names the row the server created", async () => {
  // The route persists now, so a green confirmation is the honest message - and
  // the name in it comes from the response, which is the only proof the row the
  // reader is about to see below is the one they just typed.
  VacancyService.createWithInference.mockResolvedValue({ data: CREATED });
  renderForm();
  fill(VALID);

  fireEvent.click(submit());
  await flush();

  expect(await screen.findByText("Вакансия добавлена")).toBeInTheDocument();
  expect(screen.getByText(`«${CREATED.name}» сохранена и появится в списке ниже.`)).toBeInTheDocument();
  expect(screen.queryByText("Запрос отправлен")).toBeNull();
});

test("hands the created vacancy, with its id, to the caller", async () => {
  // The id only exists in the response; the page needs it to refresh or name the
  // new row, so the form must pass the object through unchanged rather than
  // calling back with nothing.
  VacancyService.createWithInference.mockResolvedValue({ data: CREATED });
  const onCreated = jest.fn();
  renderForm({ onCreated });
  fill(VALID);

  fireEvent.click(submit());

  await waitFor(() => expect(onCreated).toHaveBeenCalledTimes(1));
  expect(onCreated).toHaveBeenCalledWith(CREATED);
  expect(onCreated.mock.calls[0][0].id).toBe(42);
});

test("calls onCreated and clears the form after a successful submit", async () => {
  VacancyService.createWithInference.mockResolvedValue({ data: CREATED });
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

test("still reports success when the response carries no body", async () => {
  // Defensive: a 204, or a proxy that drops the JSON, must not turn a stored
  // vacancy into an error or into a toast built from undefined.
  VacancyService.createWithInference.mockResolvedValue({ data: undefined });
  const onCreated = jest.fn();
  renderForm({ onCreated });
  fill(VALID);

  fireEvent.click(submit());
  await flush();

  expect(await screen.findByText("Вакансия добавлена")).toBeInTheDocument();
  expect(
    screen.getByText("Вакансия сохранена и появится в списке ниже."),
  ).toBeInTheDocument();
  expect(onCreated).toHaveBeenCalledWith(null);
});

test("keeps the server's own detail when the request is refused, and keeps the values", async () => {
  // A 4xx explains itself in response.data.detail; error.message would only say
  // "Request failed with status code 400", which tells the reader nothing.
  VacancyService.createWithInference.mockRejectedValue({
    message: "Request failed with status code 400",
    response: { status: 400, data: { detail: "Похожая вакансия уже есть" } },
  });
  renderForm();
  fill(VALID);
  fireEvent.click(submit());

  expect(await screen.findByText("Не удалось добавить вакансию")).toBeInTheDocument();
  expect(screen.getByText("Похожая вакансия уже есть")).toBeInTheDocument();
  expect(screen.queryByText("Request failed with status code 400")).toBeNull();
  // The values stay, so a corrected retry does not mean retyping the form.
  expect(field("Название вакансии")).toHaveValue(VALID.name);
});

test("reports a network failure that has no response at all", async () => {
  // No response object at all is what a dropped connection or a CORS failure
  // looks like; nothing may be read off it unguarded.
  VacancyService.createWithInference.mockRejectedValue({
    message: "Network Error",
    response: undefined,
  });
  renderForm();
  fill(VALID);
  fireEvent.click(submit());

  expect(await screen.findByText("Не удалось добавить вакансию")).toBeInTheDocument();
  expect(screen.getByText("Network Error")).toBeInTheDocument();
});

test("reports a rejected request and keeps the typed values for a retry", async () => {
  VacancyService.createWithInference.mockRejectedValue(new Error("Сервер недоступен"));
  renderForm();
  fill(VALID);
  fireEvent.click(submit());

  expect(await screen.findByText("Не удалось добавить вакансию")).toBeInTheDocument();
  expect(screen.getByText("Сервер недоступен")).toBeInTheDocument();
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
