import React from "react";
import { Button, Flex, SimpleGrid, useToast } from "@chakra-ui/react";
import { FormikProvider, useFormik } from "formik";
import * as Yup from "yup";
import FormikInput from "shared/components/ui/FormikInput";
import VacancyService from "shared/api/services/vacancyService";

// The employer's create form. It posts to
// /api/upload_vacancy/inference/employer (multipart).
//
// That route is a STUB on the server today: backend/service/services/matching.py:250-256
// returns {"message": "test"} and writes nothing, so the new vacancy does not
// appear in the table below and gets no embedding. The success toast therefore
// says "request sent", not "vacancy added" - a green "добавлено" over a row that
// never appears would send the user hunting for a bug in the form.
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
        await VacancyService.createWithInference(values);
        // A successful response only means the request was accepted. Until the
        // server persists the row, saying more than that would be a lie the user
        // discovers by scrolling down.
        toast({
          title: "Запрос отправлен",
          description:
            "Сохранение вакансий на сервере пока не реализовано — запись не появится в списке ниже.",
          status: "warning",
          duration: 7000,
        });
        resetForm();
        onCreated?.();
      } catch (error) {
        toast({
          title: "Не удалось добавить вакансию",
          description: (error && error.message) || "Неизвестная ошибка",
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
