#!/usr/bin/env node
/**
 * find-unreachable.mjs - list source files that nothing imports.
 *
 * Why this exists: the frontend arrived with ~180 files for a warehouse ERP,
 * most of them unreachable from the entry point. Deleting by hand leaves the
 * 'is this really dead?' question unanswered, and the next refactor re-opens it.
 * This walks the import graph from src/index.js the way webpack does (including
 * the jsconfig.json baseUrl='src' aliases) and prints whatever it never reached.
 *
 * Exit codes: 0 = every file under src/ is reachable (or only the allow-listed
 * ones are not), 1 = at least one unreachable file, 2 = the walk itself failed.
 *
 * Deliberately NOT a linter: it resolves static import/export specifiers and
 * require() only. A file reached solely through a dynamic import with a computed
 * path is reported as unreachable - which is the correct signal to make that path
 * static.
 */
import fs from 'node:fs';
import path from 'node:path';
import url from 'node:url';

const here = path.dirname(url.fileURLToPath(import.meta.url));
const projectRoot = path.resolve(here, '..');
const srcRoot = path.join(projectRoot, 'src');
const entry = path.join(srcRoot, 'index.js');

// Files that exist without being imported by anything.
const ALLOW_LIST = new Set([
  // CRA reads this path itself (config/paths.js testsSetup); it is never imported.
  path.join(srcRoot, 'setupTests.js'),
]);

// Jest discovers tests by filename (createJestConfig.js testMatch), not by import,
// so a *.test.js file is reachable precisely when nobody imports it.
const isTestFile = (file) => /\.(test|spec)\.[cm]?[jt]sx?$/.test(file);

const SOURCE_EXT = ['.js', '.jsx', '.mjs', '.cjs'];
// Anything a bundler can hand to a module: code, styles, and the asset types CRA
// turns into URL strings or React components.
const ASSET_EXT = [
  '.css', '.svg', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.ico',
  '.json', '.mp4', '.pdf', '.woff', '.woff2',
];

/** Every file under src/, as absolute paths. */
const walk = (dir, acc = []) => {
  for (const entryInfo of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entryInfo.name);
    if (entryInfo.isDirectory()) walk(full, acc);
    else acc.push(full);
  }
  return acc;
};

const isFile = (candidate) => {
  try {
    return fs.statSync(candidate).isFile();
  } catch {
    return false;
  }
};

const resolveFromBase = (specifier, baseDir = srcRoot) => {
  const base = path.resolve(baseDir, specifier);

  if (isFile(base)) return base;
  for (const ext of [...SOURCE_EXT, ...ASSET_EXT]) {
    if (isFile(base + ext)) return base + ext;
  }
  for (const ext of SOURCE_EXT) {
    const indexFile = path.join(base, 'index' + ext);
    if (isFile(indexFile)) return indexFile;
  }
  return null;
};

// The bare specifiers suggest.baseUrl='src' turns into src/<specifier>. Keeping
// the list explicit means a typo ('hooks/useFoo') is reported as unresolved
// instead of being silently treated as a package.
const ALIAS_PREFIXES = ['shared/', 'app/', 'pages/', 'components/', 'theme/', 'styles/'];
const ALIAS_EXACT = ['App', 'Layout'];

/**
 * Resolve one import specifier to an absolute file path, or null.
 *
 * Order mirrors webpack's resolve.modules = [appSrc, 'node_modules'] plus
 * resolve.extensions: a bare specifier is tried against src/ first, then left
 * alone as a package.
 */
const resolveSpecifier = (specifier, fromFile) => {
  if (specifier.startsWith('.')) {
    return resolveFromBase(specifier, path.dirname(fromFile));
  }
  if (specifier.startsWith('src/')) return resolveFromBase(specifier.slice(4));
  if (ALIAS_EXACT.includes(specifier)) return resolveFromBase(specifier);
  if (ALIAS_PREFIXES.some((prefix) => specifier.startsWith(prefix))) {
    return resolveFromBase(specifier);
  }
  return null; // a package: not part of the graph we are auditing
};

/** The specifiers a module asks for. Static only; see the header. */
const specifiersOf = (code) => {
  const found = new Set();
  const patterns = [
    /\bfrom\s*["']([^"']+)["']/g,
    /\bimport\s*["']([^"']+)["']/g,
    /\bimport\s*\(\s*["']([^"']+)["']\s*\)/g,
    /\brequire\s*\(\s*["']([^"']+)["']\s*\)/g,
  ];
  for (const pattern of patterns) {
    for (const match of code.matchAll(pattern)) found.add(match[1]);
  }
  return [...found];
};

const main = () => {
  if (!isFile(entry)) {
    console.error('find-unreachable: entry point not found: ' + entry);
    return 2;
  }

  const reachable = new Set();
  const queue = [entry];
  const unresolved = [];

  while (queue.length > 0) {
    const current = queue.pop();
    if (reachable.has(current)) continue;
    reachable.add(current);

    const ext = path.extname(current).toLowerCase();
    if (!SOURCE_EXT.includes(ext)) continue; // an asset: a leaf

    const code = fs.readFileSync(current, 'utf8');
    for (const specifier of specifiersOf(code)) {
      const resolved = resolveSpecifier(specifier, current);
      if (resolved === null) {
        if (specifier.startsWith('.') || ALIAS_PREFIXES.some((p) => specifier.startsWith(p))) {
          unresolved.push(current + ' -> ' + specifier);
        }
        continue;
      }
      if (!reachable.has(resolved)) queue.push(resolved);
    }
  }

  const all = walk(srcRoot);
  const unreachable = all
    .filter((file) => !reachable.has(file))
    .filter((file) => !ALLOW_LIST.has(file))
    .filter((file) => !isTestFile(file))
    .map((file) => path.relative(projectRoot, file))
    .sort();

  console.log(
    'find-unreachable: ' + reachable.size + ' reachable, ' + all.length + ' files under src/',
  );

  if (unresolved.length > 0) {
    console.log('');
    console.log('Unresolved local imports (' + unresolved.length + '):');
    for (const line of unresolved) console.log('  ' + line);
  }

  if (unreachable.length > 0) {
    console.log('');
    console.log('Unreachable (' + unreachable.length + '):');
    for (const file of unreachable) console.log('  ' + file);
    return 1;
  }

  console.log('Every file under src/ is reachable from src/index.js.');
  return 0;
};

process.exit(main());
