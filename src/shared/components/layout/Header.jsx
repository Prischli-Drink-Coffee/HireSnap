import React, { useRef } from "react";
import {
  Drawer,
  DrawerBody,
  DrawerCloseButton,
  DrawerContent,
  DrawerHeader,
  DrawerOverlay,
  Flex,
  IconButton,
  Image,
  useDisclosure,
} from "@chakra-ui/react";
import { HamburgerIcon } from "@chakra-ui/icons";
import { Link } from "react-router-dom";
import logotype from "shared/assets/images/logotype.svg";
import NavBar from "shared/components/layout/NavBar";
import { ROUTE_PATHS } from "constants.js";

// The bar itself: logo on the left, the drawer trigger on the right. The drawer
// holds the navigation because the menu is longer than a phone-width header.
const Header = () => {
  const { isOpen, onOpen, onClose } = useDisclosure();
  const btnRef = useRef();

  return (
    <Flex
      as="header"
      w="full"
      p={4}
      bg="white"
      align="center"
      justify="space-between"
      paddingLeft={["10px", "15px", "20px", "25px", "30px"]}
      paddingRight={["10px", "15px", "20px", "25px", "30px"]}
      paddingTop={5}
      paddingBottom={5}
      boxShadow="header"
    >
      {/* A router Link, not an anchor: the header renders inside the router on
          every route including /sign_in, so an <a href="/"> would reload the
          whole bundle just to reach the page it is already on. */}
      <Link to={ROUTE_PATHS.home} aria-label="HireSnap - на главную">
        <Image src={logotype} alt="HireSnap" />
      </Link>
      <IconButton
        ref={btnRef}
        aria-label="Открыть меню"
        icon={<HamburgerIcon />}
        bgColor="menu_white"
        onClick={onOpen}
      />
      <Drawer
        isOpen={isOpen}
        placement="right"
        onClose={onClose}
        finalFocusRef={btnRef}
      >
        <DrawerOverlay />
        <DrawerContent>
          <DrawerCloseButton />
          <DrawerHeader fontSize="heading">Навигация</DrawerHeader>
          <DrawerBody>
            <NavBar />
          </DrawerBody>
        </DrawerContent>
      </Drawer>
    </Flex>
  );
};

export default Header;
