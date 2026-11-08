import { useMemo } from "react";

// How many numbered page buttons are shown at once.
const PAGINATION_PAGES = 5;

// Returns the visible window of page numbers for a paginator of totalPages
// pages sitting on currentPage. The window is always contiguous and always has
// PAGINATION_PAGES entries when totalPages is large enough.
export const usePagination = (totalPages, currentPage) =>
  useMemo(() => {
    const pageArray = [];
    if (totalPages > PAGINATION_PAGES) {
      if (currentPage > 3) {
        if (totalPages - 2 < currentPage) {
          for (let i = totalPages - PAGINATION_PAGES + 1; i <= totalPages; i++) {
            pageArray.push(i);
          }
        } else {
          for (let i = currentPage - 2; i <= currentPage + 2; i++) {
            pageArray.push(i);
          }
        }
      } else {
        for (let i = 1; i <= PAGINATION_PAGES; i++) {
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
