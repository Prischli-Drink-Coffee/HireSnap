// jest-dom adds custom jest matchers for asserting on DOM nodes.
// allows you to do things like:
// expect(element).toHaveTextContent(/react/i)
// learn more: https://github.com/testing-library/jest-dom
import "@testing-library/jest-dom";

// jsdom implements no window.matchMedia, and Chakra UI's colour-mode provider
// calls it while mounting (node_modules/@chakra-ui/color-mode/src/color-mode.utils.ts).
// Without this stub EVERY test that renders <AppProviders> dies on
// "window.matchMedia is not a function" before the assertion runs. The stub is
// deliberately minimal: only the members Chakra touches, and a listener pair that
// answers "the query does not match".
if (typeof window !== "undefined" && typeof window.matchMedia !== "function") {
  window.matchMedia = (query) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  });
}

// This file MUST stay at src/setupTests.js. CRA resolves it as
// paths.testsSetup = resolveModule(resolveApp, 'src/setupTests')
// (node_modules/react-scripts/config/paths.js:74,99) and its jest rootDir is
// <rootDir>/src (scripts/utils/createJestConfig.js:26,37-40). A tests/
// directory outside src/ is not discovered, and setupFilesAfterEnv cannot be
// overridden from package.json - CRA exits 1 with "Remove it from Jest
// configuration, and put the initialization code in src/setupTests.js"
// (createJestConfig.js:110-123).
