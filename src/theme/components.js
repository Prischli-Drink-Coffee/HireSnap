import { fontSizes, radii } from "theme/tokens";

// Widget-level decisions, kept out of theme.js so the palette stays readable.
//
// Only the components this app actually renders are styled. An unreferenced
// variant is worse than a missing one: it looks available, silently renders
// nothing, and the next person copies it as if it were a working example.
export const componentStyles = {
  Tooltip: {
    baseStyle: {
      // A translucent black, not a palette colour: the tooltip must stay
      // readable over every surface without becoming a second brand accent.
      background: "rgba(0, 0, 0, 0.5)",
      padding: "5px",
      // !important because Chakra writes the tooltip label's own font-size on
      // the inner element, which a baseStyle rule cannot outrank.
      fontSize: fontSizes.body + " !important",
    },
  },
  Button: {
    variants: {
      // Primary action: the brand yellow outline that fills on hover.
      menu_yellow: {
        border: "2px solid",
        borderColor: "main_yellow",
        borderRadius: radii.none,
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
        borderRadius: radii.none,
        background: "transparent",
        _hover: {
          backgroundColor: "main_red",
        },
      },
    },
  },
};
