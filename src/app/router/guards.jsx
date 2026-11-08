import React from "react";
import { Navigate, Outlet } from "react-router-dom";
import { useCookies } from "react-cookie";
import { ACCESS_ANY_SIGNED_IN, ROUTE_PATHS } from "constants.js";

// A route guard, not a shared component: it decides whether a route element
// renders at all, so it belongs with the router that mounts it. Keeping it in
// shared/ would let any component import it and start depending on routing.
//
// userGroup is either ACCESS_ANY_SIGNED_IN (any identity) or one concrete role.
// The cookie is the only client-side authority and it is cosmetic: every route
// and every endpoint still verifies the JWT on the server.
const PrivateRoute = ({ userGroup }) => {
  const [cookie] = useCookies();

  const isAllowed =
    (userGroup === ACCESS_ANY_SIGNED_IN && Boolean(cookie.role)) ||
    userGroup === cookie.role;

  return isAllowed ? <Outlet /> : <Navigate to={ROUTE_PATHS.signIn} />;
};

export default PrivateRoute;
