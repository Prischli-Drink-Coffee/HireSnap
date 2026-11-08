import { decodeTokenPayload } from "shared/auth/decodeToken";

// Turns the token pair returned by POST /api/signin/ or /api/signup/ into the
// shape userCookie.setUser expects. The identity lives in the JWT payload
// (sub = role, user_id, type), so no extra request is needed right after login.
export const sessionFromTokens = (tokens, { email } = {}) => {
  const accessToken = tokens?.access_token || tokens?.token;
  const payload = accessToken ? decodeTokenPayload(accessToken) : null;

  return {
    token: accessToken,
    role: payload?.sub,
    userType: payload?.type,
    userId: payload?.user_id,
    userName: email,
  };
};

export default sessionFromTokens;
