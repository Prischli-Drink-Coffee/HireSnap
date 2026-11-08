import React, { useState } from "react";
import { Divider, Heading, Text, VStack } from "@chakra-ui/react";
import VacancyCreateForm from "shared/components/forms/VacancyCreateForm";
import TableVacancies from "shared/components/tables/TableVacancies";
import TableCandidates from "shared/components/tables/TableCandidates";

// The employer screen: list the vacancies, create one, and for the selected
// vacancy see the matched candidates. All three come from endpoints that exist -
// GET /api/vacancies/, POST /api/upload_vacancy/inference/employer and
// GET /api/get_all_candidates/inference/vacancy/{vacancy_id}.
const EmployerPage = () => {
  const [reloadKey, setReloadKey] = useState(0);
  const [selectedVacancy, setSelectedVacancy] = useState(null);

  return (
    <VStack
      padding={25}
      alignItems="flex-start"
      spacing="24px"
      flexGrow={1}
      width="100%"
    >
      <Heading as="h1" fontSize="22px" fontWeight={700}>
        Вакансии
      </Heading>

      <Text fontSize={14} maxW="720px">
        Список ниже показывает вакансии, которые уже есть в системе. Выберите
        вакансию, чтобы увидеть подходящих кандидатов.
      </Text>

      <VacancyCreateForm onCreated={() => setReloadKey((value) => value + 1)} />

      <Divider />

      <Heading as="h2" fontSize="18px" fontWeight={700}>
        Все вакансии
      </Heading>
      <TableVacancies
        key={reloadKey}
        onSelectVacancy={(vacancy) => setSelectedVacancy(vacancy)}
      />

      <Divider />

      <Heading as="h2" fontSize="18px" fontWeight={700}>
        {selectedVacancy
          ? `Кандидаты на вакансию «${selectedVacancy.name}»`
          : "Кандидаты"}
      </Heading>
      {selectedVacancy ? (
        <TableCandidates vacancyId={selectedVacancy.id} />
      ) : (
        <Text fontSize={14} color="light_dark">
          Выберите вакансию в таблице, чтобы увидеть подходящих кандидатов.
        </Text>
      )}
    </VStack>
  );
};

export default EmployerPage;
