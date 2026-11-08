import React from "react";
import { Navigate, Outlet } from "react-router-dom";
import { useCookies } from "react-cookie";

// userGroup is "AUTH" (any signed-in identity) or a concrete role string. The
// cookie is the only client-side authority; the backend enforces the real one.
const PrivateRoutes = ({ userGroup }) => {
  const [cookie] = useCookies();

  const isAllowed =
    (userGroup === "AUTH" && Boolean(cookie.role)) || userGroup === cookie.role;

  return isAllowed ? <Outlet /> : <Navigate to="/sign_in" />;
};

export default PrivateRoutes;
