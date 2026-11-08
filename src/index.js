import React from "react";
import ReactDOM from "react-dom/client";
import App from "App";
import AppProviders from "app/providers";

// The Montserrat weights the UI actually uses, bundled by webpack instead of
// fetched from a CDN: the app is served from an nginx container that is allowed
// to reach nothing, and a <link> to fonts.googleapis.com would silently fail in
// prod while working on a developer machine. 400 body, 600 semibold labels,
// 700 headings and the bold brand text.
import "@fontsource/montserrat/400.css";
import "@fontsource/montserrat/500.css";
import "@fontsource/montserrat/600.css";
import "@fontsource/montserrat/700.css";
import "styles/index.css";

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(
  <AppProviders>
    <App />
  </AppProviders>,
);
