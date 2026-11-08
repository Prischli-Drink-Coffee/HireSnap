import React from "react";
import { Text, VStack } from "@chakra-ui/react";
import { useCookies } from "react-cookie";
import MyLink from "shared/components/layout/MyLink";
import {
  authenticationNavItems,
  navItems,
} from "shared/components/layout/navItems";
import { ACCESS_ANY_SIGNED_IN } from "constants.js";

// The drawer's contents. Visibility is decided by the "role" cookie only: the
// menu hides what a user cannot reach, and every route and endpoint still checks
// the JWT server-side. Hiding is cosmetic, never authorization.
const NavBar = () => {
  const [cookie] = useCookies(["token", "role", "userName"]);

  const isVisible = (item) =>
    Boolean(
      item?.haveAccess?.has(cookie.role) ||
        (item?.haveAccess?.has(ACCESS_ANY_SIGNED_IN) && cookie.role),
    );

  return (
    <VStack align="flex-start">
      {navItems.filter(isVisible).map((item) => (
        <MyLink key={item.name} to={item.path} name={item.name} />
      ))}

      {authenticationNavItems.map((item) => {
        if (item.name === "Имя") {
          return isVisible(item) ? (
            <Text key={item.name} fontSize="heading" fontWeight="bold" color="highlight">
              {cookie.userName}
            </Text>
          ) : null;
        }
        return isVisible(item) ? (
          <MyLink
            key={item.name}
            onClick={item.onClick}
            to={item.path}
            name={item.name}
          />
        ) : null;
      })}
    </VStack>
  );
};

export default NavBar;
