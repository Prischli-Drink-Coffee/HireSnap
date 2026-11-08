import React, { useEffect, useState } from "react";
import { Box, Text } from "@chakra-ui/react";
import UlForTable from "shared/components/tables/UlForTable";
import CandidateService from "shared/api/services/candidateService";
import VacancyService from "shared/api/services/vacancyService";
import { useFetching } from "shared/hooks/useFetching";
import { formatSimilarity } from "shared/lib/format";
import styles from "shared/components/tables/table.module.css";

// The candidate side: GET /api/get_all_vacancies/inference/candidate/{user_id}
// returns match rows, and /api/vacancies/ supplies the names. Two requests, both
// of which exist, joined in the browser - the alternative would be a
// /candidate/{id}/vacancies endpoint that the backend does not have.
const TableMatchedVacancies = ({ userId }) => {
  const [matches, setMatches] = useState([]);

  const [load, error] = useFetching(async () => {
    const [matchesResponse, vacanciesResponse] = await Promise.all([
      CandidateService.getRecommendedVacancies(userId),
      VacancyService.getAll(),
    ]);

    const byId = new Map(
      (Array.isArray(vacanciesResponse.data) ? vacanciesResponse.data : []).map(
        (vacancy) => [vacancy.id, vacancy],
      ),
    );
    const rows = (Array.isArray(matchesResponse.data) ? matchesResponse.data : [])
      .map((match) => ({
        ...match,
        vacancy: byId.get(match.vacancy_id),
      }))
      .sort((a, b) => Number(b.distance) - Number(a.distance));

    setMatches(rows);
  });

  useEffect(() => {
    setMatches([]);
    if (userId) {
      load();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId]);

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
                <UlForTable name="Вакансия" />
              </td>
              <td className={styles.table__td}>
                <UlForTable name="Зарплата" />
              </td>
              <td className={styles.table__td}>
                <UlForTable name="Сходство" />
              </td>
            </tr>
          </thead>
          <tbody>
            {matches.map((match) => (
              <tr className={styles.table__tbody_tr} key={match.id}>
                <td className={styles.table__td}>
                  {match.vacancy?.name || `Вакансия #${match.vacancy_id}`}
                </td>
                <td className={styles.table__td}>
                  {match.vacancy?.salary || "—"}
                </td>
                <td className={styles.table__td}>
                  {formatSimilarity(match.distance)}
                </td>
              </tr>
            ))}
            {matches.length === 0 && (
              <tr>
                <td className={styles.table__empty} colSpan={3}>
                  Рекомендаций пока нет — загрузите видео, чтобы получить подборку.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}
    </Box>
  );
};

export default TableMatchedVacancies;
