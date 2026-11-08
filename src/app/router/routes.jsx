import React from "react";
import PrivateRoute from "app/router/guards";
import SignInPage from "pages/auth/SignInPage";
import SignUpPage from "pages/auth/SignUpPage";
import CandidatePage from "pages/candidate/CandidatePage";
import EmployerPage from "pages/employer/EmployerPage";
import NotFoundPage from "pages/notFound/NotFoundPage";
import Layout from "Layout";
import { ACCESS_ANY_SIGNED_IN, ROUTE_PATHS, USER_ROLES } from "constants.js";

// The route table, as data. app/router/index.js turns it into a router and
// app/router/guards.jsx owns the access check; keeping the three apart means a
// new screen is one entry here and nothing else.
//
// Paths come from constants/ROUTE_PATHS, the same object the navigation links
// read, so a link and a route cannot drift apart.
const routes = [
  {
    element: <Layout />,
    children: [
      {
        path: ROUTE_PATHS.signIn,
        element: <SignInPage />,
      },
      {
        element: <PrivateRoute userGroup={ACCESS_ANY_SIGNED_IN} />,
        children: [
          {
            path: ROUTE_PATHS.home,
            element: <CandidatePage />,
            errorElement: <NotFoundPage />,
          },
          {
            path: ROUTE_PATHS.employer,
            element: <EmployerPage />,
          },
        ],
      },
      {
        element: <PrivateRoute userGroup={USER_ROLES.admin} />,
        children: [
          {
            path: ROUTE_PATHS.signUp,
            element: <SignUpPage />,
          },
        ],
      },
    ],
  },
];

export default routes;
