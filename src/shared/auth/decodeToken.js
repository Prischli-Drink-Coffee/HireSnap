// The backend signs an RS256 access token whose payload is
//   {sub: <role>, user_id: <id>, type: <candidate|employer>, TOKEN_TYPE_FIELD}
// (backend/service/shared/tokens.py:95-103). The browser cannot verify the
// signature and
// does not try: it only needs user_id and type to address itself in the API paths
// (/upload_video/inference/{user_id}, /get_all_vacancies/inference/candidate/{user_id}).
// Everything that matters is still verified server-side on every request.
export const decodeTokenPayload = (token) => {
  try {
    const payload = String(token).split(".")[1];
    if (!payload) return null;
    const base64 = payload.replace(/-/g, "+").replace(/_/g, "/");
    const padded = base64.padEnd(base64.length + ((4 - (base64.length % 4)) % 4), "=");
    const json = decodeURIComponent(
      atob(padded)
        .split("")
        .map((char) => `%${char.charCodeAt(0).toString(16).padStart(2, "0")}`)
        .join(""),
    );
    return JSON.parse(json);
  } catch (e) {
    return null;
  }
};

export default decodeTokenPayload;
