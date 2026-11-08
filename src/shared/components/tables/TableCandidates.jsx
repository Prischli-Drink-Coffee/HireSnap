import React, { useEffect, useState } from "react";
import { Box, Text } from "@chakra-ui/react";
import UlForTable from "shared/components/tables/UlForTable";
import VacancyService from "shared/api/services/vacancyService";
import { useFetching } from "shared/hooks/useFetching";
import styles from "shared/components/tables/table.module.css";

// GET /api/get_all_candidates/inference/vacancy/{vacancy_id} returns the
// candidate-vacancy match rows for one vacancy: {id, user_id, vacancy_id,
// distance}. The browser has no endpoint that resolves user_id to a name, so the
// row can show the id and the similarity only - inventing a lookup would be a
// second request the backend does not support.
const TableCandidates = ({ vacancyId }) => {
  const [candidates, setCandidates] = useState([]);

  const [getCandidates, error] = useFetching(async () => {
    const response = await VacancyService.getRecommendedCandidates(vacancyId);
    setCandidates(Array.isArray(response.data) ? response.data : []);
  });

  useEffect(() => {
    setCandidates([]);
    if (vacancyId) {
      getCandidates();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vacancyId]);

  return (
    <Box className={styles.table__Box} display="block" width="100%">
      {error ? (
        <Text color="red.500" p={4}>
          {error}
        </Text>
      ) : (
        <table className={styles.table}>
          <thead>
            <tr className={styles.table__thead_tr}>
              <td className={styles.table__td}>
                <UlForTable name="Кандидат" />
              </td>
              <td className={styles.table__td}>
                <UlForTable name="Сходство" />
              </td>
            </tr>
          </thead>
          <tbody>
            {candidates.map((candidate) => (
              <tr className={styles.table__tbody_tr} key={candidate.id}>
                <td className={styles.table__td}>
                  Кандидат #{candidate.user_id}
                </td>
                <td className={styles.table__td}>
                  {Number(candidate.distance).toFixed(3)}
                </td>
              </tr>
            ))}
            {candidates.length === 0 && (
              <tr>
                <td className={styles.table__empty} colSpan={2}>
                  Совпадений пока нет.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}
    </Box>
  );
};

export default TableCandidates;
