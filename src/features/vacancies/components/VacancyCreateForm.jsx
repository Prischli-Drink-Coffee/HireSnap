import React from "react";
import { Button, Flex, SimpleGrid, useToast } from "@chakra-ui/react";
import { FormikProvider, useFormik } from "formik";
import * as Yup from "yup";
import FormikInput from "shared/components/ui/FormikInput";
import VacancyService from "shared/api/services/vacancyService";

// The employer's create form. It posts to
// /api/upload_vacancy/inference/employer (Form-encoded).
//
// That route persists - it embeds the text through the ml sidecar, stores the
// embedding and the row, and answers with the created vacancy {id, name, salary,
// description, skill, embedding_id}. So the toast may say the vacancy was added,
// and the created object is handed to onCreated so the list below can reload and
// show the new row instead of looking unchanged.
const validationSchema = Yup.object().shape({
  name: Yup.string().trim().required("Укажите название"),
  description: Yup.string()
    .trim()
    .min(10, "Слишком короткое описание")
    .max(2000, "Слишком длинное описание")
    .required("Укажите описание"),
  salary: Yup.string().nullable().max(100, "Слишком длинная строка"),
  skill: Yup.string().nullable().max(300, "Слишком длинная строка"),
});

const VacancyCreateForm = ({ onCreated }) => {
  const toast = useToast();

  const formik = useFormik({
    initialValues: { name: "", description: "", salary: "", skill: "" },
    validationSchema,
    onSubmit: async (values, { setSubmitting, resetForm }) => {
      try {
        const response = await VacancyService.createWithInference(values);
        const created = (response && response.data) || null;

        // The route now really stores the vacancy, so the toast may say so. The
        // name is only repeated when the server sent one back, which it does for
        // a created row; the plain message otherwise.
        toast({
          title: "Вакансия добавлена",
          description: created && created.name
            ? `«${created.name}» сохранена и появится в списке ниже.`
            : "Вакансия сохранена и появится в списке ниже.",
          status: "success",
          duration: 5000,
        });
        resetForm();
        // Hand the created vacancy over: the caller needs the id to refresh or
        // append the row, and reading it off the response here is the only place
        // that still has it.
        onCreated?.(created);
      } catch (error) {
        // A 4xx carries the server's own explanation in response.data.detail
        // ("Похожая вакансия уже есть", a validation message). error.message is
        // only the axios wrapper ("Request failed with status code 400"), so it
        // is the fallback, not the first choice.
        const detail =
          (error && error.response && error.response.data && error.response.data.detail) ||
          (error && error.message) ||
          "Неизвестная ошибка";
        toast({
          title: "Не удалось добавить вакансию",
          description: detail,
          status: "error",
          duration: 5000,
        });
      } finally {
        setSubmitting(false);
      }
    },
  });

  return (
    <FormikProvider value={formik}>
      <form onSubmit={formik.handleSubmit}>
        <SimpleGrid spacing={4} maxW="640px">
          <FormikInput formik={formik} name="name" label="Название вакансии" />
          <FormikInput formik={formik} name="description" label="Описание" />
          <FormikInput formik={formik} name="salary" label="Зарплата" />
          <FormikInput formik={formik} name="skill" label="Навыки" />
          <Flex justifyContent="flex-end">
            <Button
              variant="menu_yellow"
              type="submit"
              isLoading={formik.isSubmitting}
            >
              Добавить вакансию
            </Button>
          </Flex>
        </SimpleGrid>
      </form>
    </FormikProvider>
  );
};

export default VacancyCreateForm;
