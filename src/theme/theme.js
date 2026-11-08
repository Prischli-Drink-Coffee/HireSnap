import { extendTheme } from "@chakra-ui/react";

const theme = extendTheme({
  config: {
    initialColorMode: "light",
    useSystemColorMode: true,
  },
  fonts: {
    // Loaded by src/index.js from @fontsource/montserrat, not from a CDN: the
    // static bundle must render identically with no egress.
    body: "Montserrat, sans-serif",
  },
  // Variants for the components this app actually uses. An unreferenced variant
  // is worse than a missing one - it looks available and silently renders
  // nothing - so this block is kept to what is rendered today.
  components: {
    Tooltip: {
      baseStyle: {
        background: "rgba(0, 0, 0, 0.5)",
        padding: "5px",
        fontSize: "14px !important",
      },
    },
    Button: {
      variants: {
        // Primary action: the brand yellow outline that fills on hover.
        menu_yellow: {
          border: "2px solid",
          borderColor: "main_yellow",
          borderRadius: "0",
          background: "transparent",
          _hover: {
            backgroundColor: "main_yellow",
          },
        },
        // Destructive action. Nothing renders it today; it is kept because the
        // red is already part of the palette and a delete affordance will need
        // exactly this shape rather than a new one-off.
        menu_red: {
          border: "2px solid",
          borderColor: "main_red",
          borderRadius: "0",
          background: "transparent",
          _hover: {
            backgroundColor: "main_red",
          },
        },
      },
    },
  },
  colors: {
    main_dark: "#333",
    light_dark: "#666",
    main_yellow: "#FFBF00",
    main_red: "#FF0F00",
    menu_white: "#F8F8F8",
  },
});

export default theme;
