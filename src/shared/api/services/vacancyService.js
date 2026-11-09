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

  // POST /api/upload_vacancy/inference/employer - Form-encoded (name,
  // description, optional salary and skill), signed-in only.
  //
  // The route persists: it embeds the posted text through the ml sidecar, stores
  // the embedding and the row, and answers 200 with the created vacancy, keys
  // exactly {id, name, salary, description, skill, embedding_id}. The created row
  // therefore shows up in a later GET /vacancies/, and the caller can use the id
  // straight away. Keep posting here rather than to POST /vacancies/: this is the
  // path that also creates the embedding the recommendation routes need.
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
