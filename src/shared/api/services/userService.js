import { authUrls, usersUrl } from "shared/api/apiConsts";
import { Instance } from "shared/api/instance";
import { getToken } from "shared/auth/userCookie";

// The backend declares these routes with fastapi.Form(...) parameters, so the
// body is form-encoded, not JSON. A FormData body is accepted by FastAPI's form
// parser and keeps file and non-file submissions on the same code path.
const toFormData = (fields) => {
  const body = new FormData();
  Object.entries(fields).forEach(([key, value]) => {
    if (value !== undefined && value !== null) {
      body.append(key, value);
    }
  });
  return body;
};

export default class UserService {
  // POST /api/signin/ -> {access_token, refresh_token, token_type}
  static signIn(email, password) {
    return Instance.post(authUrls.signIn, toFormData({ email, password }));
  }

  // POST /api/signup/ -> the same token pair. type_user is "candidate" or
  // "employer"; role defaults to "user" server-side and is only widened by an
  // administrator.
  static signUp({ email, password, typeUser = "candidate", role = "user" }) {
    return Instance.post(
      authUrls.signUp,
      toFormData({ email, password, type_user: typeUser, role }),
    );
  }

  static getAll() {
    return Instance.get(usersUrl.users);
  }

  static getById(userId) {
    return Instance.get(usersUrl.byId(userId));
  }

  static getByEmail(email) {
    return Instance.get(usersUrl.byEmail(email));
  }

  static update(userId, user) {
    return Instance.put(usersUrl.update(userId), user, {
      headers: { Authorization: getToken() },
    });
  }
}
