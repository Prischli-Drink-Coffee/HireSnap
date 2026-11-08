import React, { useState } from "react";
import { Divider, Heading, Text, VStack } from "@chakra-ui/react";
import { TableVacancies, VacancyCreateForm } from "features/vacancies";
import { TableCandidates } from "features/candidates";

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
      <Heading as="h1" fontSize="heading" fontWeight={700}>
        Вакансии
      </Heading>

      <Text fontSize="body" maxW="720px">
        Список ниже показывает вакансии, которые уже есть в системе. Выберите
        вакансию, чтобы увидеть подходящих кандидатов.
      </Text>

      <VacancyCreateForm onCreated={() => setReloadKey((value) => value + 1)} />

      <Divider />

      <Heading as="h2" fontSize="subheading" fontWeight={700}>
        Все вакансии
      </Heading>
      <TableVacancies
        key={reloadKey}
        onSelectVacancy={(vacancy) => setSelectedVacancy(vacancy)}
      />

      <Divider />

      <Heading as="h2" fontSize="subheading" fontWeight={700}>
        {selectedVacancy
          ? `Кандидаты на вакансию «${selectedVacancy.name}»`
          : "Кандидаты"}
      </Heading>
      {selectedVacancy ? (
        <TableCandidates vacancyId={selectedVacancy.id} />
      ) : (
        <Text fontSize="body" color="light_dark">
          Выберите вакансию в таблице, чтобы увидеть подходящих кандидатов.
        </Text>
      )}
    </VStack>
  );
};

export default EmployerPage;
