import { extendTheme } from "@chakra-ui/react";
import { componentStyles } from "theme/components";
import { colors, fonts, fontSizes, shadows } from "theme/tokens";

// The theme is a projection of theme/tokens.js onto Chakra's scales - it decides
// WHERE a value lands, never WHAT the value is. extendTheme deep-merges, so the
// built-in scales (spacing, radii, breakpoints) survive; only the keys listed
// here are added or replaced.
//
// Read the result as the app's design contract: a component may use these keys
// (backgroundColor="menu_white", boxShadow="header", fontSize="heading") and
// must not hard-code the literal the key stands for.
const theme = extendTheme({
  config: {
    initialColorMode: "light",
    useSystemColorMode: true,
  },
  fonts: {
    body: fonts.body,
  },
  fontSizes: {
    heading: fontSizes.heading,
    subheading: fontSizes.subheading,
    body: fontSizes.body,
  },
  shadows: {
    header: shadows.header,
  },
  colors,
  components: componentStyles,
});

export default theme;
