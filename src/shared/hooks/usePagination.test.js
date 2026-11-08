import { renderHook } from "@testing-library/react";
import { usePagination } from "shared/hooks/usePagination";
import { PAGINATION } from "constants.js";

// The hook has five distinct branches. Each test below names the branch it pins,
// because the arrays are not self-explanatory: a reader comparing [2,3,4,5,6]
// with [6,7,8,9,10] needs to know which edge produced which.
const windowOf = (totalPages, currentPage) =>
  renderHook(() => usePagination(totalPages, currentPage)).result.current;

describe("usePagination", () => {
  const { pageButtonCount } = PAGINATION;

  test("lists every page when they all fit in one window", () => {
    // totalPages <= pageButtonCount: the short list branch.
    expect(windowOf(pageButtonCount, 1)).toEqual([1, 2, 3, 4, 5]);
    expect(windowOf(3, 2)).toEqual([1, 2, 3]);
    expect(windowOf(1, 1)).toEqual([1]);
  });

  test("returns an empty window when there are no pages", () => {
    expect(windowOf(0, 1)).toEqual([]);
  });

  test("starts at 1 while the reader is within the first three pages", () => {
    // currentPage <= 3 with more pages than the window: the left-anchored branch,
    // which must NOT slide left of page 1.
    expect(windowOf(10, 1)).toEqual([1, 2, 3, 4, 5]);
    expect(windowOf(10, 3)).toEqual([1, 2, 3, 4, 5]);
  });

  test("centres the window in the middle of a long list", () => {
    // The general middle branch: currentPage - 2 .. currentPage + 2.
    expect(windowOf(10, 4)).toEqual([2, 3, 4, 5, 6]);
    expect(windowOf(10, 5)).toEqual([3, 4, 5, 6, 7]);
    expect(windowOf(10, 8)).toEqual([6, 7, 8, 9, 10]);
  });

  test("anchors to the last page near the end, never past totalPages", () => {
    // totalPages - 2 < currentPage: the right-anchored branch. Page 9 of 10 would
    // centre to 7..11, so it clamps to the final window instead.
    expect(windowOf(10, 9)).toEqual([6, 7, 8, 9, 10]);
    expect(windowOf(10, 10)).toEqual([6, 7, 8, 9, 10]);
    expect(windowOf(6, 5)).toEqual([2, 3, 4, 5, 6]);
    expect(windowOf(6, 6)).toEqual([2, 3, 4, 5, 6]);
  });

  test("crosses the one-window boundary without a gap or an overlap", () => {
    // totalPages === pageButtonCount + 1: still a full window, and page 4 must
    // switch branches without repeating or skipping a number.
    expect(windowOf(pageButtonCount + 1, 1)).toEqual([1, 2, 3, 4, 5]);
    expect(windowOf(pageButtonCount + 1, 4)).toEqual([2, 3, 4, 5, 6]);
    expect(windowOf(pageButtonCount + 1, 6)).toEqual([2, 3, 4, 5, 6]);
  });

  // The invariant, not a case: this is the reason the width lives in constants.
  // If the window ever came back shorter or longer than pageButtonCount, the
  // paginator would render a page the reader cannot reach - or leave one out.
  test("always returns exactly pageButtonCount entries on a long list", () => {
    for (let currentPage = 1; currentPage <= 20; currentPage += 1) {
      expect(windowOf(20, currentPage)).toHaveLength(pageButtonCount);
    }
  });

  test("always returns a contiguous ascending run ending at totalPages at the end", () => {
    const last = windowOf(20, 20);
    expect(last[last.length - 1]).toBe(20);
    for (let index = 1; index < last.length; index += 1) {
      expect(last[index]).toBe(last[index - 1] + 1);
    }
  });
});
