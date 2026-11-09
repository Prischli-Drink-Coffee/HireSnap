import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { ChakraProvider } from "@chakra-ui/react";
import Pagination from "shared/components/pagination/Pagination";
import { PAGINATION } from "constants.js";

// The component owns two pieces of arithmetic that nothing else checks: the
// "shown X - Y of N" range and the page recomputation when the page size changes.
// A wrong range is a lie about the data, so every branch below is pinned.
const makeProps = (overrides = {}) => ({
  className: "",
  currentPage: 1,
  setCurrentPage: jest.fn(),
  totalPages: 3,
  currentPageSize: 10,
  setCurrentPageSize: jest.fn(),
  totalCountItem: 25,
  ...overrides,
});

const renderPagination = (overrides) => {
  const props = makeProps(overrides);
  const view = render(
    <ChakraProvider>
      <Pagination {...props} />
    </ChakraProvider>,
  );
  return { ...props, ...view };
};

// The range is split across several text nodes by JSX interpolation, so the
// assertion normalises the container text once instead of matching a node.
const rangeText = (container) => container.textContent.replace(/\s+/g, " ");

const sizeInput = () => screen.getByLabelText("Размер страницы");

test("shows the range of the first page", () => {
  const { container } = renderPagination({ currentPage: 1, currentPageSize: 10, totalCountItem: 25 });

  expect(rangeText(container)).toContain("Показано 1 – 10 из 25");
});

test("stops the range at the last row instead of overrunning the data", () => {
  const { container } = renderPagination({ currentPage: 3, currentPageSize: 10, totalCountItem: 25 });

  expect(rangeText(container)).toContain("Показано 21 – 25 из 25");
});

test("falls back to the shared page size while the field is empty", () => {
  const { container } = renderPagination({ currentPageSize: "", totalCountItem: 100 });

  expect(sizeInput()).toHaveValue("");
  expect(rangeText(container)).toContain(`Показано 1 – ${PAGINATION.fallbackPageSize} из 100`);
});

test("clamps a page size larger than the row count to the row count", () => {
  // Typing 500 into a 25-row table must not claim "1 – 500 of 25".
  const { container } = renderPagination({ currentPageSize: 500, totalCountItem: 25 });

  expect(rangeText(container)).toContain("Показано 1 – 25 из 25");
});

test("disables the paging buttons that cannot go anywhere", () => {
  renderPagination({ currentPage: 1, totalPages: 3 });

  expect(screen.getByLabelText("Первая страница")).toBeDisabled();
  expect(screen.getByRole("button", { name: "1" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "3" })).toBeEnabled();
  expect(screen.getByLabelText("Последняя страница")).toBeEnabled();
});

test("disables both arrows when there are no pages at all", () => {
  renderPagination({ currentPage: 1, totalPages: 0, totalCountItem: 0 });

  expect(screen.getByLabelText("Первая страница")).toBeDisabled();
  expect(screen.getByLabelText("Последняя страница")).toBeDisabled();
});

test("asks the parent for the page that was clicked", () => {
  const setCurrentPage = jest.fn();
  renderPagination({ currentPage: 1, setCurrentPage });

  fireEvent.click(screen.getByRole("button", { name: "3" }));

  expect(setCurrentPage).toHaveBeenCalledWith(3);
});

test("the arrows jump to the first and last page", () => {
  const setCurrentPage = jest.fn();
  renderPagination({ currentPage: 2, totalPages: 3, setCurrentPage });

  fireEvent.click(screen.getByLabelText("Первая страница"));
  fireEvent.click(screen.getByLabelText("Последняя страница"));

  expect(setCurrentPage).toHaveBeenNthCalledWith(1, 1);
  expect(setCurrentPage).toHaveBeenNthCalledWith(2, 3);
});

test("rejects a non-numeric page size without touching the parent", () => {
  const setCurrentPage = jest.fn();
  const setCurrentPageSize = jest.fn();
  renderPagination({ setCurrentPage, setCurrentPageSize });

  fireEvent.change(sizeInput(), { target: { value: "1a" } });

  expect(setCurrentPageSize).not.toHaveBeenCalled();
  expect(setCurrentPage).not.toHaveBeenCalled();
});

test("rejects a leading zero, which is what a stray keystroke produces", () => {
  const setCurrentPageSize = jest.fn();
  renderPagination({ setCurrentPageSize });

  fireEvent.change(sizeInput(), { target: { value: "05" } });

  expect(setCurrentPageSize).not.toHaveBeenCalled();
});

test("keeps the reader on the same first visible row when the size changes", () => {
  // Page 2 at 10 rows starts at row 20; at 5 rows that row is on page 4.
  const setCurrentPage = jest.fn();
  const setCurrentPageSize = jest.fn();
  renderPagination({ currentPage: 2, currentPageSize: 10, totalCountItem: 100, setCurrentPage, setCurrentPageSize });

  fireEvent.change(sizeInput(), { target: { value: "5" } });

  expect(setCurrentPageSize).toHaveBeenCalledWith("5");
  expect(setCurrentPage).toHaveBeenCalledWith(4);
});

test("clearing the size returns to the first page", () => {
  const setCurrentPage = jest.fn();
  const setCurrentPageSize = jest.fn();
  renderPagination({ currentPage: 4, setCurrentPage, setCurrentPageSize });

  fireEvent.change(sizeInput(), { target: { value: "" } });

  expect(setCurrentPageSize).toHaveBeenCalledWith("");
  expect(setCurrentPage).toHaveBeenCalledWith(1);
});
