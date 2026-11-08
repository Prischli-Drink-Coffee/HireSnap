import React from "react";
import { Box, Button, Input, Text, VStack, useToast } from "@chakra-ui/react";
import { useFormik } from "formik";
import { useNavigate } from "react-router-dom";
import { ReactComponent as LoginIcon } from "shared/assets/images/authorization.svg";
import UserService from "shared/api/services/userService";
import { setUser } from "shared/auth/userCookie";
import { sessionFromTokens } from "shared/auth/session";
import { ROUTE_PATHS } from "constants.js";

const validate = (values) => {
  const errors = {};

  if (!values.email) {
    errors.email = "Укажите почту";
  } else if (!/\S+@\S+\.\S+/.test(values.email)) {
    errors.email = "Некорректный адрес почты";
  }
  if (!values.password) {
    errors.password = "Укажите пароль";
  }

  return errors;
};

const SignInPage = () => {
  const navigate = useNavigate();
  const toast = useToast();

  // POST /api/signin/ takes form fields email + password and answers with
  // {access_token, refresh_token, token_type}. The identity (role, user_id,
  // type) rides inside the access token, so the cookie is filled from the JWT.
  const signIn = async (values) => {
    try {
      const response = await UserService.signIn(values.email, values.password);
      setUser(sessionFromTokens(response.data, { email: values.email }));
      navigate(ROUTE_PATHS.home);
    } catch (error) {
      console.error("Error signIn:", error);
      toast({
        title: "Не удалось войти",
        description:
          (error && error.response && error.response.data && error.response.data.detail) ||
          "Проверьте почту и пароль",
        status: "error",
        duration: 5000,
      });
    }
  };

  const formik = useFormik({
    initialValues: { email: "", password: "" },
    validate,
    onSubmit: signIn,
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
        as={LoginIcon}
        width="100%"
        height="100%"
        position="absolute"
        top="0"
        left="0"
        zIndex="-1"
      />

      <VStack spacing="20px" align="center" justify="center">
        <VStack
          spacing="20px"
          align="stretch"
          border="1px solid #ddd"
          p="20px"
          borderRadius="md"
          background="rgba(255, 255, 255, 0.8)"
          minW={["280px", "320px"]}
        >
          <Text fontSize="2xl" fontWeight="bold" textAlign="center">
            Вход в систему
          </Text>

          <form onSubmit={formik.handleSubmit}>
            <VStack spacing="15px" align="stretch">
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

              <Button
                type="submit"
                colorScheme="teal"
                size="lg"
                width="full"
                isLoading={formik.isSubmitting}
              >
                Войти
              </Button>
            </VStack>
          </form>
        </VStack>
      </VStack>
    </Box>
  );
};

export default SignInPage;
