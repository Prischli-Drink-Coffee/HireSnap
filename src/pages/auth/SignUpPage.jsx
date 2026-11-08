import React from "react";
import {
  Box,
  Button,
  Input,
  Radio,
  RadioGroup,
  SimpleGrid,
  Stack,
  Text,
  useToast,
} from "@chakra-ui/react";
import { useFormik } from "formik";
import { useNavigate } from "react-router-dom";
import { ROUTE_PATHS } from "constants.js";
import { ReactComponent as RegistrationIcon } from "shared/assets/images/registration.svg";
import UserService from "shared/api/services/userService";
import { setUser } from "shared/auth/userCookie";
import { sessionFromTokens } from "shared/auth/session";

// POST /api/signup/ requires type_user, which the server restricts to
// "candidate" or "employer" (backend/service/models/db/db_models.py:30-35, TypeUser). The form
// therefore asks for the account type instead of inventing a default.
const validate = (values) => {
  const errors = {};

  if (!values.email) {
    errors.email = "Укажите почту";
  } else if (!/\S+@\S+\.\S+/.test(values.email)) {
    errors.email = "Некорректный адрес почты";
  }
  if (!values.password) {
    errors.password = "Укажите пароль";
  } else if (values.password.length < 6) {
    errors.password = "Пароль короче 6 символов";
  }

  return errors;
};

const SignUpPage = () => {
  const navigate = useNavigate();
  const toast = useToast();

  const signUp = async (values) => {
    try {
      const response = await UserService.signUp({
        email: values.email,
        password: values.password,
        typeUser: values.typeUser,
      });
      setUser(sessionFromTokens(response.data, { email: values.email }));
      navigate(ROUTE_PATHS.home);
    } catch (error) {
      console.error("Error signing up:", error);
      toast({
        title: "Не удалось создать пользователя",
        description:
          (error && error.response && error.response.data && error.response.data.detail) ||
          "Неизвестная ошибка",
        status: "error",
        duration: 5000,
      });
    }
  };

  const formik = useFormik({
    initialValues: { email: "", password: "", typeUser: "candidate" },
    validate,
    onSubmit: signUp,
  });

  return (
    <Box
      position="relative"
      width="100vw"
      height="100vh"
      display="flex"
      alignItems="center"
      justifyContent="center"
      overflow="hidden"
    >
      {/* The SVG fills the window and sits behind the card. */}
      <Box
        as={RegistrationIcon}
        width="100%"
        height="100%"
        position="absolute"
        top="0"
        left="0"
        zIndex="-1"
      />

      <Box
        border="1px solid #ddd"
        p={10}
        borderRadius="md"
        background="rgba(255, 255, 255, 0.7)"
      >
        <form onSubmit={formik.handleSubmit}>
          <SimpleGrid minW="300px" spacing={5}>
            <Text fontSize="2xl" fontWeight="bold">
              Регистрация
            </Text>

            <Input
              id="email"
              name="email"
              type="email"
              placeholder="Почта"
              onChange={formik.handleChange}
              value={formik.values.email}
              isInvalid={formik.errors.email && formik.touched.email}
            />
            {formik.errors.email && formik.touched.email && (
              <Text color="red.500">{formik.errors.email}</Text>
            )}

            <Input
              id="password"
              name="password"
              type="password"
              placeholder="Пароль"
              onChange={formik.handleChange}
              value={formik.values.password}
              isInvalid={formik.errors.password && formik.touched.password}
            />
            {formik.errors.password && formik.touched.password && (
              <Text color="red.500">{formik.errors.password}</Text>
            )}

            <RadioGroup
              name="typeUser"
              value={formik.values.typeUser}
              onChange={(value) => formik.setFieldValue("typeUser", value)}
            >
              <Stack direction="row" spacing={6}>
                <Radio value="candidate">Кандидат</Radio>
                <Radio value="employer">Работодатель</Radio>
              </Stack>
            </RadioGroup>

            <Button
              type="submit"
              colorScheme="teal"
              size="lg"
              isLoading={formik.isSubmitting}
            >
              Создать
            </Button>
          </SimpleGrid>
        </form>
      </Box>
    </Box>
  );
};

export default SignUpPage;
