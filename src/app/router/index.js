import { createHashRouter } from "react-router-dom";
import routes from "app/router/routes";

// createHashRouter, not createBrowserRouter: the bundle is served as static
// files by nginx (frontend/nginx.conf), which has no SPA fallback for deep
// paths. A hash router keeps every route inside index.html.
const router = createHashRouter(routes);

export default router;
