import { vacanciesUrl } from "shared/api/apiConsts";
import { Instance } from "shared/api/instance";
import { getToken } from "shared/auth/userCookie";

export default class VacancyService {
  // GET /api/vacancies/ - the plain CRUD collection. It is NOT paginated by the
  // server, so the table paginates the returned array in the browser.
  static getAll() {
    return Instance.get(vacanciesUrl.vacancies);
  }

  static getById(vacancyId) {
    return Instance.get(vacanciesUrl.byId(vacancyId));
  }

  static update(vacancyId, vacancy) {
    return Instance.put(vacanciesUrl.update(vacancyId), vacancy, {
      headers: { Authorization: getToken() },
    });
  }

  // POST /api/upload_vacancy/inference/employer - multipart form (name,
  // description, optional salary and skill).
  //
  // WARNING - this route is a stub today: the backend's
  // service/services/matching/application/matching_service.py:343-353 returns the
  // literal {"message": "test"} and persists nothing, so a vacancy
  // created here DOES NOT appear in GET /vacancies/ afterwards. The UI says so
  // out loud rather than implying the row was saved. Do not "fix" it here by
  // switching to POST /vacancies/: that skips the embedding the recommendation
  // routes need, and the backend route is the thing that has to change.
  static createWithInference({ name, description, salary, skill }) {
    const body = new FormData();
    body.append("name", name);
    body.append("description", description);
    if (salary) body.append("salary", salary);
    if (skill) body.append("skill", skill);

    return Instance.post(vacanciesUrl.createWithInference, body, {
      headers: { Authorization: getToken() },
    });
  }

  // GET /api/get_all_candidates/inference/vacancy/{id} - candidates ranked for
  // one vacancy.
  static getRecommendedCandidates(vacancyId) {
    return Instance.get(vacanciesUrl.recommendedCandidates(vacancyId), {
      headers: { Authorization: getToken() },
    });
  }
}
