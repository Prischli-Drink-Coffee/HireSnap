import React from "react";
import { Text, VStack } from "@chakra-ui/react";

const NotFoundPage = () => {
  return (
    <VStack minH="100vh" justify="center" spacing="12px">
      <Text fontSize="2xl" fontWeight="bold">
        404
      </Text>
      <Text fontSize={14} color="light_dark">
        Страница не найдена
      </Text>
    </VStack>
  );
};

export default NotFoundPage;
