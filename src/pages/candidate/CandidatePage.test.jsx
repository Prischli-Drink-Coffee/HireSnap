import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ChakraProvider } from "@chakra-ui/react";
import CandidatePage from "pages/candidate/CandidatePage";
import CandidateService from "shared/api/services/candidateService";

// The page is the only caller of POST /upload_video/inference/{user_id}. Its job
// is small and entirely about consequences: the id comes from the session, the
// file goes to the service once, and the match table is asked to refresh only
// after the upload succeeded.
jest.mock("shared/hooks/useCurrentUser", () => ({
  __esModule: true,
  default: jest.fn(),
}));

jest.mock("shared/api/services/candidateService", () => ({
  __esModule: true,
  default: { uploadVideo: jest.fn() },
}));

// The real table fetches two endpoints on mount; this page only owns the remount
// key, so it is replaced by a probe that records how often it was mounted.
const mockTableRenders = [];
jest.mock("features/candidates", () => {
  const React = require("react");
  const actual = jest.requireActual("features/candidates");

  const TableMatchedVacanciesProbe = (props) => {
    mockTableRenders.push(props.userId);
    return React.createElement("div", null, "matched vacancies table");
  };
  TableMatchedVacanciesProbe.displayName = "TableMatchedVacanciesProbe";

  return { ...actual, TableMatchedVacancies: TableMatchedVacanciesProbe };
});

const useCurrentUser = require("shared/hooks/useCurrentUser").default;

const renderPage = () =>
  render(
    <ChakraProvider>
      <CandidatePage />
    </ChakraProvider>,
  );

// fireEvent wraps its own dispatch in act(); the work that escapes is what the
// page and Chakra's toast schedule afterwards. Draining one microtask inside
// act() in a second step settles it without wrapping the Testing Library call
// itself, which eslint-plugin-testing-library forbids.
const flush = () =>
  act(async () => {
    await Promise.resolve();
  });

const submit = () => screen.getByRole("button", { name: "Загрузить видео" });

const pickVideo = async (name = "intro.mp4") => {
  const file = new File(["video-bytes"], name, { type: "video/mp4" });
  fireEvent.change(screen.getByLabelText(/Видео о себе/), { target: { files: [file] } });
  await flush();
  return file;
};

const clickSubmit = async () => {
  fireEvent.click(submit());
  await flush();
};

beforeEach(() => {
  mockTableRenders.length = 0;
  CandidateService.uploadVideo.mockReset();
  useCurrentUser.mockReset();
  useCurrentUser.mockReturnValue({ user: { userId: 7 }, loading: false });
});

test("renders the heading, the instructions and an enabled control for a signed-in user", () => {
  renderPage();

  expect(screen.getByRole("heading", { name: "Подбор вакансий" })).toBeInTheDocument();
  expect(screen.getByText(/Загрузите короткое видео о себе/)).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Подходящие вакансии" })).toBeInTheDocument();
  expect(submit()).toBeDisabled();
});

test("uploads the file with the id from the session, not one typed into the page", async () => {
  CandidateService.uploadVideo.mockResolvedValue({ data: {} });
  renderPage();
  const file = await pickVideo();

  await clickSubmit();

  await waitFor(() => expect(CandidateService.uploadVideo).toHaveBeenCalledTimes(1));
  expect(CandidateService.uploadVideo).toHaveBeenCalledWith(7, file);
});

test("confirms the upload and does not promise an instant match", async () => {
  CandidateService.uploadVideo.mockResolvedValue({ data: {} });
  renderPage();
  await pickVideo();

  await clickSubmit();

  expect(await screen.findByText("Видео загружено")).toBeInTheDocument();
  expect(screen.getByText(/Обработка занимает время/)).toBeInTheDocument();
});

test("remounts the match table after a successful upload, so a fresh answer is fetched", async () => {
  CandidateService.uploadVideo.mockResolvedValue({ data: {} });
  renderPage();
  expect(mockTableRenders).toHaveLength(1);

  await pickVideo();
  await clickSubmit();

  await waitFor(() => expect(mockTableRenders).toHaveLength(2));
  expect(mockTableRenders).toEqual([7, 7]);
});

test("does not remount the table when the upload fails", async () => {
  CandidateService.uploadVideo.mockRejectedValue(new Error("boom"));
  renderPage();
  await pickVideo();

  await clickSubmit();

  expect(await screen.findByText("Не удалось загрузить видео")).toBeInTheDocument();
  expect(mockTableRenders).toHaveLength(1);
});

test("surfaces the server's own message when it sends one", async () => {
  CandidateService.uploadVideo.mockRejectedValue({
    response: { data: { detail: "Файл слишком большой" } },
  });
  renderPage();
  await pickVideo();

  await clickSubmit();

  expect(await screen.findByText("Файл слишком большой")).toBeInTheDocument();
});

test("falls back to the error message when the response carries no detail", async () => {
  CandidateService.uploadVideo.mockRejectedValue(new Error("Network Error"));
  renderPage();
  await pickVideo();

  await clickSubmit();

  expect(await screen.findByText("Network Error")).toBeInTheDocument();
});

test("an anonymous visitor gets a disabled control and no request", () => {
  useCurrentUser.mockReturnValue({ user: null, loading: false });
  renderPage();

  expect(submit()).toBeDisabled();
  expect(CandidateService.uploadVideo).not.toHaveBeenCalled();
});

test("asks for a sign-in instead of uploading without an id", async () => {
  // The disabled button hides this branch from the UI, so the guard is only
  // reachable if a future caller forgets isDisabled. Pinned because a silent
  // no-op here would be indistinguishable from a slow upload.
  useCurrentUser.mockReturnValue({ user: null, loading: false });
  const { rerender } = render(
    <ChakraProvider>
      <CandidatePage />
    </ChakraProvider>,
  );
  expect(submit()).toBeDisabled();

  useCurrentUser.mockReturnValue({ user: {}, loading: false });
  rerender(
    <ChakraProvider>
      <CandidatePage />
    </ChakraProvider>,
  );

  expect(submit()).toBeDisabled();
  expect(CandidateService.uploadVideo).not.toHaveBeenCalled();
});
