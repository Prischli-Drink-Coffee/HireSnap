import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ChakraProvider } from "@chakra-ui/react";
import MediaUploader from "features/uploads/components/MediaUploader";

// The picker owns the <input> and the client-side state only; the endpoint is the
// caller's business. So the test watches the one seam: what onUpload receives, and
// that the control is reusable afterwards.
const renderUploader = (props = {}) =>
  render(
    <ChakraProvider>
      <MediaUploader onUpload={jest.fn()} {...props} />
    </ChakraProvider>,
  );

const picker = () => screen.getByLabelText(/Загрузить файл/);
const submitButton = () => screen.getByRole("button", { name: "Отправить на сервер" });

// fireEvent already wraps its own dispatch in act(); what escapes is the work
// the component schedules afterwards. Draining one microtask inside act() in a
// SECOND step settles it, and keeps the Testing Library call out of the block -
// eslint-plugin-testing-library bans wrapping its utils in act directly.
const flush = () =>
  act(async () => {
    await Promise.resolve();
  });

const clickSubmit = async () => {
  fireEvent.click(submitButton());
  await flush();
};

const pickFile = async (input, name = "clip.mp4") => {
  const file = new File(["video-bytes"], name, { type: "video/mp4" });
  fireEvent.change(input, { target: { files: [file] } });
  await flush();
  return file;
};

test("disables the submit button until a file is chosen", () => {
  renderUploader();

  expect(submitButton()).toBeDisabled();
  expect(screen.queryByText(/Выбран файл/)).toBeNull();
});

test("names the chosen file and enables the button", async () => {
  renderUploader();
  await pickFile(picker());

  expect(screen.getByText("Выбран файл: clip.mp4")).toBeInTheDocument();
  expect(submitButton()).toBeEnabled();
});

test("hands the File to onUpload, exactly once, and clears the selection after it resolves", async () => {
  const onUpload = jest.fn().mockResolvedValue(undefined);
  renderUploader({ onUpload });
  const file = await pickFile(picker());

  await clickSubmit();

  await waitFor(() => expect(onUpload).toHaveBeenCalledTimes(1));
  expect(onUpload).toHaveBeenCalledWith(file);
  await waitFor(() => expect(screen.queryByText(/Выбран файл/)).toBeNull());
});

test("resets the DOM input value, so the same file can be picked twice in a row", async () => {
  // Without clearing input.value the browser fires no change event for a second
  // selection of the same file, and the user has to pick something else first.
  const onUpload = jest.fn().mockResolvedValue(undefined);
  renderUploader({ onUpload });
  const input = picker();
  await pickFile(input);

  await clickSubmit();
  await waitFor(() => expect(onUpload).toHaveBeenCalled());

  expect(input.value).toBe("");
});

test("respects a caller that disables the control while it works", async () => {
  renderUploader({ isDisabled: true });
  await pickFile(picker());

  expect(submitButton()).toBeDisabled();
});

test("renders the labels the caller passes, and the helper text only when given", () => {
  renderUploader({
    label: "Видео о себе",
    buttonLabel: "Загрузить видео",
    helperText: "Поддерживаются mp4, mov, avi, webm.",
  });

  expect(screen.getByText("Видео о себе")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Загрузить видео" })).toBeInTheDocument();
  expect(screen.getByText("Поддерживаются mp4, mov, avi, webm.")).toBeInTheDocument();
});

test("omits the helper text when the caller passes none", () => {
  renderUploader();

  expect(screen.queryByText(/Поддерживаются/)).toBeNull();
});

test("an empty FileList clears the pending choice instead of crashing", async () => {
  // The handler reads files?.[0] ?? null, so an empty list is a null selection.
  // Worth pinning: it is the shape a cleared input produces, and a throw here
  // would take the whole upload card down.
  renderUploader();
  const input = picker();
  await pickFile(input, "first.mp4");

  fireEvent.change(input, { target: { files: [] } });
  await flush();

  expect(screen.queryByText(/Выбран файл/)).toBeNull();
  expect(submitButton()).toBeDisabled();
});
