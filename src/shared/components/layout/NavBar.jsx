import React from "react";
import { Text, VStack } from "@chakra-ui/react";
import { useCookies } from "react-cookie";
import MyLink from "shared/components/layout/MyLink";
import { authenticationPaths, paths } from "shared/components/layout/paths";

// The drawer's contents. Visibility is decided by the "role" cookie only: the
// menu hides what a user cannot reach, and every route and endpoint still checks
// the JWT server-side. Hiding is cosmetic, never authorization.
const NavBar = () => {
  const [cookie] = useCookies(["token", "role", "userName"]);

  const isVisible = (item) =>
    Boolean(
      item?.haveAccess?.has(cookie.role) ||
        (item?.haveAccess?.has("AUTH") && cookie.role),
    );

  return (
    <VStack align="flex-start">
      {paths.filter(isVisible).map((item) => (
        <MyLink key={item.name} to={item.path} name={item.name} />
      ))}

      {authenticationPaths.map((item) => {
        if (item.name === "Имя") {
          return isVisible(item) ? (
            <Text key={item.name} fontSize={22} fontWeight="bold" color="#1a8dff">
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
