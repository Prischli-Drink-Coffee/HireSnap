import React, { useEffect, useMemo, useState } from "react";
import { Box, Text } from "@chakra-ui/react";
import Pagination from "shared/components/pagination/Pagination";
import UlForTable from "shared/components/tables/UlForTable";
import VacancyService from "shared/api/services/vacancyService";
import { useFetching } from "shared/hooks/useFetching";
import useWindowDimensions from "shared/hooks/useWindowDimensions";
import { PAGINATION } from "constants.js";
import styles from "shared/components/tables/table.module.css";

// GET /api/vacancies/ is a plain collection with no paging parameters, so the
// whole list is fetched once and split in the browser. That is honest for this
// backend: the endpoint cannot page, and pretending otherwise would show an
// empty second page forever.
const TableVacancies = ({ onSelectVacancy }) => {
  const [vacancies, setVacancies] = useState([]);
  const [sortAsc, setSortAsc] = useState(true);
  const [currentPage, setCurrentPage] = useState(1);
  const [currentPageSize, setCurrentPageSize] = useState(
    PAGINATION.defaultPageSize,
  );
  const { width } = useWindowDimensions();

  const [getVacancies, error] = useFetching(async () => {
    const response = await VacancyService.getAll();
    setVacancies(Array.isArray(response.data) ? response.data : []);
  });

  useEffect(() => {
    getVacancies();
    // The fetch is intentionally once per mount: there is no server-side page to
    // ask for.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const sorted = useMemo(() => {
    const rows = [...vacancies];
    rows.sort((a, b) => {
      const left = (a.name || "").toLowerCase();
      const right = (b.name || "").toLowerCase();
      return sortAsc ? left.localeCompare(right) : right.localeCompare(left);
    });
    return rows;
  }, [vacancies, sortAsc]);

  const pageSize =
    currentPageSize === ""
      ? PAGINATION.fallbackPageSize
      : Number(currentPageSize) || PAGINATION.fallbackPageSize;
  const totalPages = Math.max(1, Math.ceil(sorted.length / pageSize));
  const page = Math.min(currentPage, totalPages);
  const visible = sorted.slice((page - 1) * pageSize, page * pageSize);

  return (
    <Box className={styles.table__Box} display="block" width="100%">
      {error ? (
        <Text color="red.500" p={4}>
          {error}
        </Text>
      ) : (
        <table
          className={styles.table}
          width={
            width <= PAGINATION.horizontalScrollWidth
              ? PAGINATION.horizontalScrollWidth + "px"
              : "100%"
          }
        >
          <thead>
            <tr className={styles.table__thead_tr}>
              <td className={styles.table__td}>
                <UlForTable name="№" />
              </td>
              <td>
                <UlForTable
                  sort={sortAsc}
                  setSort={setSortAsc}
                  name="Название вакансии"
                />
              </td>
              <td className={styles.table__td}>
                <UlForTable name="Зарплата" />
              </td>
              <td className={styles.table__td}>
                <UlForTable name="Навыки" />
              </td>
              <td className={styles.table__td}>
                <UlForTable name="" />
              </td>
            </tr>
          </thead>
          <tbody>
            {visible.map((vacancy, index) => (
              <tr className={styles.table__tbody_tr} key={vacancy.id}>
                <td className={styles.table__td}>
                  {(page - 1) * pageSize + index + 1}.
                </td>
                <td className={styles.table__td}>{vacancy.name}</td>
                <td className={styles.table__td}>{vacancy.salary || "—"}</td>
                <td className={styles.table__td}>{vacancy.skill || "—"}</td>
                <td className={styles.table__td}>
                  <Text
                    as="button"
                    color="main_dark"
                    textDecoration="underline"
                    onClick={() => onSelectVacancy?.(vacancy)}
                  >
                    Кандидаты
                  </Text>
                </td>
              </tr>
            ))}
            {visible.length === 0 && (
              <tr>
                <td className={styles.table__empty} colSpan={5}>
                  Вакансий пока нет.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}
      <Pagination
        totalCountItem={sorted.length}
        className={styles.table__footer}
        currentPageSize={currentPageSize}
        setCurrentPageSize={setCurrentPageSize}
        currentPage={page}
        totalPages={totalPages}
        setCurrentPage={setCurrentPage}
      />
    </Box>
  );
};

export default TableVacancies;
