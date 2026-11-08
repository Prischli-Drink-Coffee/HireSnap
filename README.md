# HireSnap frontend

Create React App (react-scripts 5.0.1, React 18, Chakra UI) single-page app for
HireSnap. It talks to the FastAPI backend through the gateway, which rewrites
`/api/` to the backend's `/server/` mount.

## Scripts

| command | what it does |
| --- | --- |
| `npm start` | dev server on <http://localhost:3000> |
| `npm run build` | production bundle into `build/` |
| `npm test` | Jest via react-scripts (watch mode; add `-- --watchAll=false` in CI) |
| `npm run lint` | `eslint src --ext .js,.jsx`, the same rules CRA compiles with |
| `npm run find:unreachable` | walks the import graph from `src/index.js` and prints files nothing imports; exits 1 if any exist |
| `npm run eject` | CRA eject (irreversible; not used by this project) |

`npm run build` is deliberately **not** a lint gate: `frontend/docker/Dockerfile`
does not set `CI`, because `CI=true` turns every ESLint warning into a build
error. Use `npm run lint` for that gate.

## Layout

```
src/
  index.js              createRoot + AppProviders + <App />
  App.js                renders the router only
  Layout.jsx            header / modal helper / <Outlet /> / footer
  app/                  providers.jsx (Chakra + cookies), router.jsx
  pages/                one directory per screen (auth, candidate, employer, notFound)
  shared/
    api/                apiConsts.js (base URL + route table), instance.js (axios),
                        services/ one module per backend resource
    auth/               userCookie.js, decodeToken.js, session.js, PrivateRoutes.jsx
    components/         layout/, tables/, forms/, media/, pagination/, ui/, feedback/
    hooks/              useFetching, usePagination, useWindowDimensions, useCurrentUser
    assets/images/      the SVGs the components import
  styles/index.css      the only global stylesheet
  setupTests.js         Jest setup - must stay at this path (CRA hardcodes it)
scripts/find-unreachable.mjs
```

Imports are absolute from `src/`: `jsconfig.json` sets `compilerOptions.baseUrl` to
`src`, which CRA feeds to both webpack (`resolve.modules` + aliases) and Jest
(`modulePaths`). So `import Footer from "shared/components/layout/Footer"` works
without `../../..`. CRA rejects any other `baseUrl` value (`config/modules.js`).

## API base URL

`src/shared/api/apiConsts.js` resolves it once, first match wins:

1. `REACT_APP_BASE_URL` (inlined at build time by react-scripts, so it is fixed
   when the image is built - see `ARG REACT_APP_BASE_URL` in
   `frontend/docker/Dockerfile`);
2. `window.location.origin + "/api/"` when the bundle is served from a
   non-loopback host;
3. `http://localhost:8080/api/` as the local development fallback.

Every path in that module is a route that exists in the backend today. Nothing
here invents an endpoint: if the server does not serve it, the UI does not call
it.

## Routing

`createHashRouter`, not `createBrowserRouter`: the bundle is static files behind
nginx, which has no SPA fallback for deep paths. A hash router keeps every route
inside `index.html`.

## Tests

`npm test` runs Jest with `src/setupTests.js` (which imports
`@testing-library/jest-dom`). Test files live beside the code as `*.test.js` -
CRA only looks inside `src/`, and `setupFilesAfterEnv` cannot be relocated away
from `src/setupTests.js`.

Two things in the configuration are not decoration:

- `jest.moduleNameMapper` in `package.json` maps `axios` to its CommonJS
  browser build (`dist/browser/axios.cjs`). axios 1.x ships ESM behind
  `exports.browser`, which Jest 27 cannot parse; the mapper is one of the few
  Jest keys CRA lets a project override.
- `src/setupTests.js` stubs `window.matchMedia`, which jsdom does not
  implement and `@chakra-ui/color-mode` calls while mounting. Without it every
  render test fails before it asserts anything.

Both are explained in place in the files themselves.

## Docker

`frontend/docker/Dockerfile` builds the bundle in a `node:18-alpine` stage and
copies `build/` into an `nginx:1.27-alpine` runtime that serves it with
`frontend/nginx.conf`. The stack itself is started from the repository root with
`./docker/run.sh --dev up` or `--prod up`; see the root `README.md`.

The Montserrat weights are bundled from `@fontsource/montserrat` by
`src/index.js` rather than linked from a font CDN. The runtime container is on a
network with no egress, so a `fonts.googleapis.com` `<link>` would work on a
developer machine and silently fall back to a system font in production.
