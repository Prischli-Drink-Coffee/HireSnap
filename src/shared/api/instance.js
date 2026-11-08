import axios from "axios";
import { baseUrl } from "shared/api/apiConsts";

// The single axios instance. Nothing else in the app may create its own client:
// one base URL, one place to add interceptors.
export const Instance = axios.create({
  baseURL: baseUrl,
});

export default Instance;
