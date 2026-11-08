import { useEffect, useState } from "react";
import { useCookies } from "react-cookie";
import UserService from "shared/api/services/userService";
import { deleteUser, getUserId, setUser } from "shared/auth/userCookie";

// Concurrent callers (Layout plus the current page) must not each fire the same
// GET /users/user_id/{id}: two identical requests on every navigation is pure
// noise. Only the IN-FLIGHT promise is shared, not the result - a later mount
// re-reads the profile, which is what keeps a changed name or role visible
// without a reload.
let inFlight = null;
let inFlightUserId = null;

const fetchUser = (userId) => {
  if (inFlight && inFlightUserId === userId) return inFlight;

  inFlightUserId = userId;
  inFlight = UserService.getById(userId).finally(() => {
    inFlight = null;
    inFlightUserId = null;
  });
  return inFlight;
};

// Resolves the signed-in identity once per mount and mirrors it into cookies, so
// every other component can read the role synchronously instead of calling
// /users/ again. The role is read from the cookie when the id is missing, which
// is the state right after sign-in.
export default function useCurrentUser() {
  const [cookie] = useCookies(["token"]);
  const [user, setCurrentUser] = useState(null);
  const [loading, setLoading] = useState(Boolean(cookie.token));

  useEffect(() => {
    let cancelled = false;
    const userId = getUserId();

    if (!cookie.token || userId === undefined) {
      setLoading(false);
      return () => {
        cancelled = true;
      };
    }

    setLoading(true);
    fetchUser(userId)
      .then((response) => {
        if (cancelled) return;
        const me = response.data || {};
        setCurrentUser(me);
        setUser({
          role: me.role,
          userType: me.type,
          userId: me.id,
          userName: me.email,
        });
      })
      .catch((error) => {
        // 401 means the token is gone or expired: drop the cached identity so the
        // router falls back to the sign-in page instead of looping on a request
        // that can never succeed. Anything else is transient and leaves the
        // cookie alone.
        if (error && error.response && error.response.status === 401) {
          deleteUser();
        }
        console.error("Error loading current user:", error);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [cookie.token]);

  return { user, loading };
}
