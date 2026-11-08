# Frontend design contract

This file is a map, not a tutorial: it states where code goes, what may import
what, and which rules the build enforces. Read it before adding a directory.

## Shape

```
src/
  index.js            the entry point; the only file that mounts React
  App.js              the router, and nothing else
  Layout.jsx          header + <Outlet /> + footer, the frame every page renders in
  constants.js        values two or more modules must agree on
  setupTests.js       CRA reads this path itself (see below)
  app/
    providers.jsx     CookiesProvider > ChakraProvider > ColorModeScript
    router/
      index.js        createHashRouter(routes)
      routes.jsx      the route table, as data
      guards.jsx      PrivateRoute - the one access check
  pages/              one directory per screen: auth, candidate, employer, notFound
  features/           one directory per domain: vacancies, candidates, uploads
  shared/
    api/              apiConsts.js (base URL + route table), instance.js (axios),
                      services/ (one module per backend resource)
    auth/             cookie, JWT decode, session helpers
    assets/images/    imported SVGs
    components/       layout, ui, feedback, pagination, tables (generic pieces)
    hooks/            useFetching, usePagination, useCurrentUser, useWindowDimensions
    lib/              pure helpers with no React (format.js)
  theme/
    tokens.js         raw values: colours, fonts, sizes, shadow, radii
    components.js     Chakra component recipes built from the tokens
    theme.js          extendTheme - decides which Chakra scale a token lands on
  styles/index.css    the two global rules CSS modules cannot express
```

## Layering

`pages -> features -> shared`.

- A **page** is a route target. It composes features, owns nothing reusable, and
  may import from `shared/` and `features/`.
- A **feature** is one domain. It owns the components that only make sense with
  that domain's data (`features/vacancies` owns the vacancy table and the create
  form). A feature may import from `shared/`, **never** from another feature and
  never from a page. Two features that need the same code mean that code belongs
  in `shared/`.
- **shared/** is the bottom of the stack: it imports nothing from `pages/` or
  `features/`. If a `shared/` module needs a feature, the dependency is upside
  down.

Each feature exposes a **barrel** (`features/vacancies/index.js`) and pages import
from the barrel, never from `features/vacancies/components/...`. That is what lets
a component move inside its feature without touching a page.

Imports are absolute from `src/` (`jsconfig.json` sets `baseUrl: "src"`), so a file
says `import TableVacancies from "features/vacancies"` and never `../../../`.

## Theme contract

`theme/tokens.js` holds **only** values - no Chakra keys, no component names.
`theme/theme.js` is the single place that maps a value onto a Chakra scale, and
`theme/components.js` the single place that turns values into widget recipes.

A component therefore writes `backgroundColor="menu_white"`,
`boxShadow="header"`, `fontSize="heading"` - never the literal the token stands for.
A value used by one component stays a literal in that component; a token nobody
reuses is indirection, not design.

Because the mapping lives in `theme.js`, a `theme/` module may **not** import a
component, a page or a feature. It is a leaf like `constants.js`.

## API layer

One axios instance: `shared/api/instance.js`. One place that decides the base URL:
`shared/api/apiConsts.js` (build-time `REACT_APP_BASE_URL`, then
`window.location.origin + "/api/"` for a non-loopback host, then
`http://localhost:8080/api/`). One module per backend resource under
`shared/api/services/`.

Do not create a second client, do not call `fetch` from a component, and do not
hard-code a path a service module could own. The gateway rewrites `/api/` to
`/server/`, so the browser always speaks `/api/...`.

## Rules the build checks

- Every file under `src/` must be reachable from `src/index.js`:
  `npm run find:unreachable` walks the import graph and fails on an orphan. A file
  nothing imports is dead, even if it looks finished.
- `npm run lint` must pass. The build is deliberately **not** a lint gate: CRA turns
  every ESLint warning into an error under `CI=true`, and `frontend/docker/Dockerfile`
  does not set `CI`, so warnings stay warnings in the image.
- No `.less`. Styling is Chakra props, CSS modules (`*.module.css`) or the two
  global rules in `styles/index.css`.

## What react-scripts fixes for us

The build is plain `react-scripts 5.0.1`; there is no craco, no webpack override and
no babel plugin of our own. That constrains the tree in ways worth writing down, so
nobody re-discovers them by breaking the build:

- `src/setupTests.js` cannot move. `react-scripts/config/paths.js` resolves that exact
  path, and `createJestConfig.js` exits 1 if the file is missing. It is allow-listed in
  `find-unreachable.mjs` because nothing imports it.
- Jest only looks inside `src/` (`roots: ["<rootDir>/src"]`, and `testMatch` is
  sandboxed to it), so a `tests/` directory outside `src/` would never run.
- `jest` resolves `baseUrl` through `config/modules.js`, but only for specifiers that
  look like directories it can map (`shared/...`, `app/...`). A bare root module such
  as `constants` is NOT mapped by Jest even though webpack resolves it, so every
  importer writes `from "constants.js"` - an explicit path both resolvers agree on. Spelled `from "constants"` the suite fails with
  `SyntaxError: Unexpected token 'export'` on `src/constants.js:19`.
- `baseUrl: "src"` works for webpack **and** Jest only because it is exactly
  `src/`; `react-scripts/config/modules.js` throws on any other value. That is why the
  alias is a bare `shared/` prefix rather than GPTHub's `@shared/` - the `@` form
  needs a custom resolver, i.e. craco, which we are not adding.
- `ModuleScopePlugin` rejects imports from outside `src/`, so a shared component may
  not live in a sibling directory.
- The router is a **hash** router (`createHashRouter`): nginx serves the bundle as
  static files with no SPA fallback, so a browser-router deep link would 404 on
  refresh.

## Design tokens at a glance

| Token | Value | Where |
| --- | --- | --- |
| `menu_white` | `#F8F8F8` | page background |
| `footer_surface` | `#FFF7EE` | footer band |
| `main_dark` / `light_dark` | `#333` / `#666` | primary / secondary copy |
| `main_yellow` / `main_red` | `#FFBF00` / `#FF0F00` | primary / destructive action |
| `highlight` | `#1a8dff` | the signed-in user's name |
| `fonts.body` | Montserrat | bundled via `@fontsource`, never a CDN |
| `fontSizes` | 22 / 18 / 14 px | heading / subheading / body |
| `shadows.header` | soft 25px halo | header and footer |
