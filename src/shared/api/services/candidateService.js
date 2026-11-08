import { candidatesUrl } from "shared/api/apiConsts";
import { Instance } from "shared/api/instance";
import { getToken } from "shared/auth/userCookie";

export default class CandidateService {
  // POST /api/upload_video/inference/{user_id} - multipart: "file" plus the
  // optional name / phone / date_birth / gender fields the route accepts.
  static uploadVideo(userId, file, details = {}) {
    const body = new FormData();
    body.append("file", file);
    Object.entries(details).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== "") {
        body.append(key, value);
      }
    });

    return Instance.post(candidatesUrl.uploadVideo(userId), body, {
      headers: { Authorization: getToken() },
    });
  }

  // GET /api/get_all_vacancies/inference/candidate/{user_id} - vacancies ranked
  // for the signed-in candidate.
  static getRecommendedVacancies(userId) {
    return Instance.get(candidatesUrl.recommendedVacancies(userId), {
      headers: { Authorization: getToken() },
    });
  }
}
