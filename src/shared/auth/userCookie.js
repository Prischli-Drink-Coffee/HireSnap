import Cookies from "js-cookie";

// A decade. The token itself is what expires; the cookie is only the client-side
// cache of it, and a stale cookie is corrected by Layout's /users/me round-trip.
const COOKIE_OPTIONS = { expires: 365 ** 2 };

export const getToken = () => {
  const token = Cookies.get("token");
  if (token === undefined) {
    return token;
  }
  return `Bearer ${token}`;
};

export const getRole = () => Cookies.get("role");

export const getUserId = () => {
  const userId = Cookies.get("userId");
  return userId === undefined ? undefined : Number(userId);
};

// The token pair comes back as {access_token, refresh_token, token_type}; the
// identity fields come from a separate /users/ lookup. setUser writes whichever
// of the two it is handed, so both call sites share one cookie shape.
export const setUser = ({ token, accessToken, role, userType, userId, userName }) => {
  const access = token || accessToken;
  if (access !== undefined) {
    Cookies.set("token", access, COOKIE_OPTIONS);
  }
  if (role !== undefined) {
    Cookies.set("role", role, COOKIE_OPTIONS);
  }
  if (userType !== undefined) {
    Cookies.set("userType", userType, COOKIE_OPTIONS);
  }
  if (userId !== undefined && userId !== null) {
    Cookies.set("userId", userId, COOKIE_OPTIONS);
  }
  if (userName !== undefined) {
    Cookies.set("userName", userName, COOKIE_OPTIONS);
  }
};

export const deleteUser = () => {
  Cookies.remove("token");
  Cookies.remove("role");
  Cookies.remove("userType");
  Cookies.remove("userId");
  Cookies.remove("userName");
};
