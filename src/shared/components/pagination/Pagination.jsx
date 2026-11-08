import React from "react";
import { Image, Input, Text } from "@chakra-ui/react";
import chevronLeft from "shared/assets/images/chevron-left-pagination.svg";
import chevronRight from "shared/assets/images/chevron-right-pagination.svg";
import { usePagination } from "shared/hooks/usePagination";
import { PAGINATION } from "constants.js";
import styles from "shared/components/pagination/pagination.module.css";

const Pagination = ({
  className,
  currentPage,
  setCurrentPage,
  totalPages,
  currentPageSize,
  setCurrentPageSize,
  totalCountItem,
}) => {
  const pages = usePagination(totalPages, currentPage);

  // An empty box means the fallback size; a page size larger than the row count is
  // clamped so the "shown X – Y of N" range never overruns the data. Both numbers
  // come from constants because the table slices its rows with them too.
  const pageSize =
    currentPageSize === ""
      ? PAGINATION.fallbackPageSize
      : Math.min(currentPageSize, totalCountItem || 1);

  // Changing the page size keeps the reader at the same row: the old offset is
  // recomputed against the new size, then clamped into [1, lastPage].
  const changePageSize = (value) => {
    // Digits only, no leading zero, empty allowed while the user is typing.
    const isNumeric = /^\d*$/.test(value);
    if (!isNumeric || (value !== "" && value[0] === "0")) {
      return;
    }

    const firstVisibleRow = currentPage * pageSize;
    const newSize = Number(value);
    const lastPage = newSize > 0 ? Math.floor(totalCountItem / newSize) : 1;

    setCurrentPageSize(value);
    if (value === "" || !isFinite(newSize) || newSize === 0) {
      setCurrentPage(1);
      return;
    }
    const targetPage = Math.floor(firstVisibleRow / newSize);
    setCurrentPage(
      firstVisibleRow > totalCountItem
        ? Math.max(1, Math.min(lastPage, lastPage || 1))
        : Math.max(1, targetPage || 1),
    );
  };

  return (
    <div className={`${className} ${styles.Pagination}`}>
      <Text fontSize={[13, 14]}>
        Показано {(currentPage - 1) * pageSize + 1} –{" "}
        {currentPage * pageSize > totalCountItem
          ? totalCountItem
          : currentPage * pageSize}{" "}
        из {totalCountItem}
      </Text>
      <div className={styles.Pagination__buttons}>
        <button
          className={styles.Pagination__button}
          disabled={currentPage === 1}
          onClick={() => setCurrentPage(1)}
          aria-label="Первая страница"
        >
          <Image src={chevronLeft} alt="" />
        </button>
        {pages.map((page) => (
          <button
            className={styles.Pagination__button}
            disabled={page === currentPage}
            key={page}
            onClick={() => setCurrentPage(page)}
          >
            {page}
          </button>
        ))}
        <button
          className={styles.Pagination__button}
          disabled={currentPage === totalPages || totalPages === 0}
          onClick={() => setCurrentPage(totalPages)}
          aria-label="Последняя страница"
        >
          <Image src={chevronRight} alt="" />
        </button>
      </div>
      <div className={styles.Pagination__display_change}>
        <Text fontSize={[13, 14]} pr={15} whiteSpace="nowrap">
          Показывать на странице:
        </Text>
        <Input
          borderColor="main_yellow"
          focusBorderColor="main_yellow"
          _hover={{ borderColor: "main_yellow" }}
          value={currentPageSize}
          onChange={(event) => changePageSize(event.target.value)}
          height={8}
          width="60px"
          aria-label="Размер страницы"
        />
      </div>
    </div>
  );
};

export default Pagination;
