import React from "react";
import { RouterProvider } from "react-router-dom";
import router from "app/router";

// App owns nothing but the router. The route table lives in app/router/routes.jsx,
// the router itself in app/router/index.js and the providers (Chakra, cookies,
// colour mode) in app/providers.jsx, so a test can mount one half without the other.
function App() {
  return <RouterProvider router={router} />;
}

export default App;
