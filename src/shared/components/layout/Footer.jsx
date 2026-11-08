import React from "react";
import { HStack, Image, Text } from "@chakra-ui/react";
import up from "shared/assets/images/up.svg";

// The old footer credited the design studio that built the warehouse product;
// this build has no reason to carry another company's branding.
const Footer = () => (
  <HStack
    backgroundColor="#FFF7EE"
    width="100%"
    minHeight={["60px", "70px", "80px", "90px", "100px"]}
    boxShadow="0px 0px 25px 0px rgba(0, 0, 0, 0.10)"
    padding={["10px", "12px", "15px", "20px", "25px"]}
  >
    <HStack justify="space-between" width="100%" align="center">
      <Text color="light_dark" fontSize={["12px", "14px", "16px", "16px", "16px"]}>
        HireSnap © 2024
      </Text>
      <HStack onClick={() => window.scrollTo(0, 0)}>
        <Text
          color="light_dark"
          fontSize={["12px", "14px", "16px", "16px", "16px"]}
        >
          Вверх страницы
        </Text>
        <Image src={up} alt="" />
      </HStack>
    </HStack>
  </HStack>
);

export default Footer;
