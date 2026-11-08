import { deleteUser } from "shared/auth/userCookie";
import { ACCESS_ANY_SIGNED_IN, ROUTE_PATHS, USER_ROLES } from "constants.js";

// The drawer menu, as data rather than markup.
//
// The PATHS come from constants: the router owns the route table, and a menu
// entry is a pointer into it, not a second copy. The LABELS and the access sets
// stay here because they are presentation - "what the drawer offers" is a
// property of the layout, and no other module reads them.
//
// haveAccess is a Set of role names plus the ACCESS_ANY_SIGNED_IN sentinel;
// NavBar hides what the reader cannot reach. Hiding is cosmetic, never
// authorization - the guard and the server decide.
export const navItems = [
  {
    path: ROUTE_PATHS.home,
    name: "Главная",
    haveAccess: new Set([ACCESS_ANY_SIGNED_IN]),
  },
  {
    path: ROUTE_PATHS.employer,
    name: "Работодателю",
    haveAccess: new Set([ACCESS_ANY_SIGNED_IN]),
  },
];

// The account entries.
//
// "Имя" has no path on purpose: NavBar renders it as plain text (the signed-in
// user's name) instead of a link, which is why the name is the discriminator
// there.
export const authenticationNavItems = [
  {
    path: ROUTE_PATHS.signIn,
    name: "Вход",
    haveAccess: new Set([undefined]),
  },
  {
    path: ROUTE_PATHS.signIn,
    name: "Выход",
    onClick: deleteUser,
    haveAccess: new Set([ACCESS_ANY_SIGNED_IN]),
  },
  {
    path: ROUTE_PATHS.signUp,
    name: "Регистрация",
    haveAccess: new Set([USER_ROLES.admin]),
  },
  {
    name: "Имя",
    haveAccess: new Set([ACCESS_ANY_SIGNED_IN]),
  },
];
