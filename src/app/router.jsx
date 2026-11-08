import React from "react";
import { createHashRouter } from "react-router-dom";

import SignInPage from "pages/auth/SignInPage";
import SignUpPage from "pages/auth/SignUpPage";
import CandidatePage from "pages/candidate/CandidatePage";
import EmployerPage from "pages/employer/EmployerPage";
import NotFoundPage from "pages/notFound/NotFoundPage";
import PrivateRoutes from "shared/auth/PrivateRoutes";
import Layout from "Layout";

// createHashRouter, not createBrowserRouter: the bundle is served as static
// files by nginx (frontend/nginx.conf), which has no SPA fallback for deep
// paths. A hash router keeps every route inside index.html.
const router = createHashRouter([
  {
    element: <Layout />,
    children: [
      {
        path: "/sign_in",
        element: <SignInPage />,
      },
      {
        element: <PrivateRoutes userGroup="AUTH" />,
        children: [
          {
            path: "/",
            element: <CandidatePage />,
            errorElement: <NotFoundPage />,
          },
          {
            path: "/employer",
            element: <EmployerPage />,
          },
        ],
      },
      {
        element: <PrivateRoutes userGroup="ADMIN" />,
        children: [
          {
            path: "/signup_page",
            element: <SignUpPage />,
          },
        ],
      },
    ],
  },
]);

export default router;
