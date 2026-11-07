import { banks } from "./banks";

// The dadata API token used to live here as a string literal, twice. It was a real
// credential committed to a public repository and it is still in this file's git
// history, so it has to be rotated at dadata regardless of this change.
//
// A literal cannot be fixed by a scanner: gitleaks and every other secret gate
// report it and then the next commit re-adds it. The value now comes from the
// build-time environment (CRA inlines REACT_APP_* into the bundle), and the same
// key is declared in docker/.env.example so a deployment has one place to set it.
//
// A missing token is NOT an error here: these two helpers feed an optional
// requisites autocomplete, and a form that cannot suggest a company name must
// still be fillable by hand. The callers already treat a rejected or empty
// response as "no suggestions".
const DADATA_TOKEN = (() => {
  try {
    return (process.env.REACT_APP_DADATA_TOKEN || "").trim();
  } catch (error) {
    // process is undefined outside the CRA build (plain node, some test runners).
    return "";
  }
})();

export const getRequisitesInn = (inn) => {
  if (!DADATA_TOKEN) {
    return Promise.resolve({ suggestions: [] });
  }
  let url = "http://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/party";
  let token = DADATA_TOKEN;

  let options = {
    method: "POST",
    mode: "cors",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      Authorization: "Token " + token,
    },
    body: JSON.stringify({ query: inn }),
  };

  const result = fetch(url, options).then((response) => response.json());
  return result;
};

export const getBankName = (bic) => {
  let result = banks.find((elem) => `0${elem.Bic}` == bic);
  return result.ShortName;
};

export const getCSCHbyBic = (bic) => {
  if (!DADATA_TOKEN) {
    return Promise.resolve({ suggestions: [] });
  }
  let url = "http://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/bank";
  let token = DADATA_TOKEN;

  let options = {
    method: "POST",
    mode: "cors",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      Authorization: "Token " + token,
    },
    body: JSON.stringify({ query: bic }),
  };

  const result = fetch(url, options).then((response) => response.json());
  return result
};
