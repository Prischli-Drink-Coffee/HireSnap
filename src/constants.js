// App-level constants that more than one module needs.
//
// The test for "does this belong here" is deliberately strict: a value used by a
// single module stays in that module, because a constants file that collects
// one-off literals becomes a second, worse definition of the code it describes.
// Every group below names its readers, and a group that loses its second reader
// should move back out.
//
// Nothing here imports from src/: constants are leaves, so importing them can
// never create a cycle with the code that consumes them.

// Route paths, in one place.
//
// Read by app/router/routes.jsx (the table itself), shared/components/layout/
// navItems.js (the links), app/router/guards.jsx and the two auth pages (where a
// successful sign-in lands) and Header.jsx (the logo). A path spelled twice
// drifts exactly once, and the symptom is a link that navigates to the
// not-found page.
export const ROUTE_PATHS = {
  home: "/",
  employer: "/employer",
  signIn: "/sign_in",
  signUp: "/signup_page",
};

// The sentinel a menu item and a route guard use for "any signed-in identity",
// as opposed to one concrete role.
//
// Read by navItems.js (the haveAccess sets) and app/router/guards.jsx (the
// userGroup prop). If the two spell it differently, the menu shows a link that
// the guard immediately bounces back to the sign-in page.
export const ACCESS_ANY_SIGNED_IN = "AUTH";

// Role names a screen may gate on.
//
// The value mirrors the "role" claim of the access token, which lands in the
// cookie verbatim (shared/auth/session.js reads payload.sub). The backend's
// RoleEnum is lowercase ("user" | "admin"), so the value here MUST be lowercase
// too: the guard and the menu compare with strict equality, and "ADMIN" against
// a cookie holding "admin" silently closed both the registration link and the
// /signup_page route to every real administrator.
export const USER_ROLES = {
  admin: "admin",
};

// Table pagination, shared by the table that owns the state and the two pieces
// that render it.
//
// defaultPageSize  - the page size a table starts at (features/vacancies).
// fallbackPageSize - what an emptied page-size box means. Both the table (which
//                    slices the rows) and Pagination (which prints "shown X - Y
//                    of N") read it; if they disagree, the counter describes a
//                    different slice than the one on screen.
// pageButtonCount  - the width of the numbered window (shared/hooks/usePagination).
// horizontalScrollWidth - the viewport width under which a table keeps its full
//                    width and scrolls horizontally instead of squeezing the
//                    columns (features/vacancies).
export const PAGINATION = {
  defaultPageSize: 10,
  fallbackPageSize: 5,
  pageButtonCount: 5,
  horizontalScrollWidth: 944,
};
