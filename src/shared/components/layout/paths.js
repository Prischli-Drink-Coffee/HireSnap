import { deleteUser } from "shared/auth/userCookie";

export const roles = {
  ADMIN: "ADMIN",
  CANDIDATE: "CANDIDATE",
  EMPLOYER: "EMPLOYER",
};

// Only routes that exist in app/router.jsx. The warehouse build listed fourteen
// menu entries for fourteen screens; ten of them are not part of this app, and a
// menu item that navigates to the not-found page is worse than a short menu.
export const paths = [
  { path: "/", name: "Главная", haveAccess: new Set(["AUTH"]) },
  { path: "/employer", name: "Работодателю", haveAccess: new Set(["AUTH"]) },
];

// The account entries. "Имя" has no path on purpose: NavBar renders it as plain
// text (the signed-in user's name) instead of a link.
export const authenticationPaths = [
  { path: "/sign_in", name: "Вход", haveAccess: new Set([undefined]) },
  {
    path: "/sign_in",
    name: "Выход",
    onClick: deleteUser,
    haveAccess: new Set(["AUTH"]),
  },
  {
    path: "/signup_page",
    name: "Регистрация",
    haveAccess: new Set([roles.ADMIN]),
  },
  {
    name: "Имя",
    haveAccess: new Set(["AUTH"]),
  },
];
