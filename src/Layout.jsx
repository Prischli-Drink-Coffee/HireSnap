import React from "react";
import { VStack } from "@chakra-ui/react";
import { Outlet } from "react-router-dom";
import Header from "shared/components/layout/Header";
import Footer from "shared/components/layout/Footer";
import HelperButtonModal from "shared/components/feedback/HelperButtonModal";
import useCurrentUser from "shared/hooks/useCurrentUser";

function Layout() {
  // The identity is loaded (and, when the cookie is missing or stale, refreshed)
  // by the hook so the pages below and the navigation read one source.
  useCurrentUser();

  return (
    <VStack backgroundColor="menu_white" width="100%" minH="100VH">
      <Header />
      <HelperButtonModal />
      <Outlet />
      <Footer />
    </VStack>
  );
}

export default Layout;
