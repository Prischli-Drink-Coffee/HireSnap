import { useMemo } from "react";
import { PAGINATION } from "constants.js";

// Returns the visible window of page numbers for a paginator of totalPages
// pages sitting on currentPage. The window is always contiguous and always has
// PAGINATION.pageButtonCount entries when totalPages is large enough.
//
// The width lives in constants because the table that owns the state and this
// hook that slices it must agree: a 4-wide window over a 5-sized page list is a
// page number the reader can never reach.
export const usePagination = (totalPages, currentPage) =>
  useMemo(() => {
    const { pageButtonCount } = PAGINATION;
    const pageArray = [];
    if (totalPages > pageButtonCount) {
      if (currentPage > 3) {
        if (totalPages - 2 < currentPage) {
          for (let i = totalPages - pageButtonCount + 1; i <= totalPages; i++) {
            pageArray.push(i);
          }
        } else {
          for (let i = currentPage - 2; i <= currentPage + 2; i++) {
            pageArray.push(i);
          }
        }
      } else {
        for (let i = 1; i <= pageButtonCount; i++) {
          pageArray.push(i);
        }
      }
    } else {
      for (let i = 1; i <= totalPages; i++) {
        pageArray.push(i);
      }
    }
    return pageArray;
  }, [totalPages, currentPage]);
