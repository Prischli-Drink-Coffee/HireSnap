import React from "react";
import { ChakraProvider, ColorModeScript } from "@chakra-ui/react";
import { CookiesProvider } from "react-cookie";
import theme from "theme/theme";

// Every context the tree needs, in one place. Order matters: CookiesProvider is
// outermost because react-cookie is read by Layout, PrivateRoutes and the
// services, all of which render or run inside ChakraProvider.
const AppProviders = ({ children }) => (
  <CookiesProvider defaultSetOptions={{ path: "/" }}>
    <ChakraProvider theme={theme}>
      <ColorModeScript initialColorMode={theme.config.initialColorMode} />
      {children}
    </ChakraProvider>
  </CookiesProvider>
);

export default AppProviders;
