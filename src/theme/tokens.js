// The raw design values.
//
// Nothing in this file knows about Chakra: no theme keys, no component names, no
// recipes. theme.js is the only file that decides which Chakra scale a value
// lands on, and theme/components.js the only file that turns values into widget
// recipes. The point of the split is that a value has exactly one definition and
// a reviewer can audit the palette without reading any component markup.
//
// Colour keys keep the underscore names the components already use as Chakra
// props (backgroundColor="menu_white"), so the palette is not a second naming
// scheme on top of the first.

export const colors = {
  // Surfaces
  menu_white: "#F8F8F8", // the page background (Layout)
  footer_surface: "#FFF7EE", // the footer band, warmer than the page

  // Text
  main_dark: "#333", // body copy
  light_dark: "#666", // secondary copy, table placeholders

  // Accents. Only these two are interactive: yellow is the primary action, red
  // is the destructive one, and neither is used as decoration.
  main_yellow: "#FFBF00",
  main_red: "#FF0F00",
  highlight: "#1a8dff", // the signed-in user's name in the drawer
};

// Loaded by src/index.js from @fontsource/montserrat rather than from a CDN: the
// bundle is served by an nginx container that may reach nothing, so an external
// font request would fail in production while working on a developer machine.
export const fonts = {
  body: "Montserrat, sans-serif",
};

// The three sizes the screens genuinely share. A size used by one component
// stays a literal in that component - a token nobody reuses is indirection.
export const fontSizes = {
  heading: "22px", // page title, drawer header, the user name
  subheading: "18px", // section title inside a page
  body: "14px", // running copy
};

// The one elevation in the app: the soft halo under the header and the footer.
export const shadows = {
  header: "0px 0px 25px 0px rgba(0, 0, 0, 0.10)",
};

// The outline buttons are deliberately square (see theme/components.js).
export const radii = {
  none: "0",
};
