#!/usr/bin/env node

import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const requireFromWeb = createRequire(fileURLToPath(new URL("../../web/package.json", import.meta.url)));
const ts = requireFromWeb("typescript");

function fail(message) {
  process.stderr.write(`${message}\n`);
  process.exit(1);
}

function parseArgs(argv) {
  const result = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!key?.startsWith("--") || value === undefined) {
      fail("expected --source-root PATH --tsconfig PATH");
    }
    result[key.slice(2)] = value;
  }
  if (Object.keys(result).sort().join(",") !== "source-root,tsconfig") {
    fail("expected exactly --source-root PATH --tsconfig PATH");
  }
  return result;
}

function readStdin() {
  const raw = fs.readFileSync(0, "utf8");
  let value;
  try {
    value = JSON.parse(raw);
  } catch (error) {
    fail(`stdin is not valid JSON: ${error.message}`);
  }
  if (
    !value ||
    typeof value !== "object" ||
    Array.isArray(value) ||
    Object.keys(value).join(",") !== "files" ||
    !Array.isArray(value.files) ||
    value.files.some((item) => typeof item !== "string")
  ) {
    fail("stdin must be exactly {\"files\": [string, ...]}");
  }
  return value.files;
}

function readCompilerOptions(tsconfigPath) {
  const loaded = ts.readConfigFile(tsconfigPath, ts.sys.readFile);
  if (loaded.error) {
    fail(ts.flattenDiagnosticMessageText(loaded.error.messageText, "\n"));
  }
  const parsed = ts.parseJsonConfigFileContent(
    loaded.config,
    ts.sys,
    path.dirname(tsconfigPath),
    undefined,
    tsconfigPath,
  );
  if (parsed.errors.length > 0) {
    fail(parsed.errors.map((error) => ts.flattenDiagnosticMessageText(error.messageText, "\n")).join("; "));
  }
  return parsed.options;
}

function isInside(candidate, root) {
  const relative = path.relative(root, candidate);
  return relative !== "" && !relative.startsWith(`..${path.sep}`) && relative !== ".." && !path.isAbsolute(relative);
}

function normalizedId(filename, sourceRoot) {
  let relative = path.relative(sourceRoot, filename).split(path.sep).join("/");
  relative = relative.replace(/\.d\.ts$/u, "");
  relative = relative.replace(/\.(?:tsx?|jsx?|json)$/u, "");
  return relative;
}

function aliasMatches(specifier, key) {
  const star = key.indexOf("*");
  if (star === -1) {
    return specifier === key;
  }
  return specifier.startsWith(key.slice(0, star)) && specifier.endsWith(key.slice(star + 1));
}

function isInternalSpecifier(specifier, sourceRoot, options) {
  if (specifier.startsWith(".") || path.isAbsolute(specifier)) {
    return true;
  }
  if (Object.keys(options.paths ?? {}).some((key) => aliasMatches(specifier, key))) {
    return true;
  }
  const first = specifier.split("/", 1)[0];
  return fs.readdirSync(sourceRoot, { withFileTypes: true }).some((entry) => {
    const name = entry.isDirectory() ? entry.name : entry.name.replace(/\.(?:tsx?|jsx?)$/u, "");
    return name === first;
  });
}

const args = parseArgs(process.argv.slice(2));
const sourceRoot = path.resolve(args["source-root"]);
const tsconfigPath = path.resolve(args.tsconfig);
const files = readStdin().map((item) => path.resolve(item));
const options = readCompilerOptions(tsconfigPath);
const dependencies = new Map();
const errors = [];

function addSpecifier(sourceFile, specifier) {
  const resolved = ts.resolveModuleName(specifier, sourceFile.fileName, options, ts.sys).resolvedModule;
  if (!resolved) {
    const explicitExtension = path.posix.extname(specifier);
    if (explicitExtension && ![".ts", ".tsx", ".js", ".jsx", ".json"].includes(explicitExtension)) {
      return;
    }
    if (isInternalSpecifier(specifier, sourceRoot, options)) {
      errors.push(`${normalizedId(sourceFile.fileName, sourceRoot)}: unresolved internal import ${specifier}`);
    }
    return;
  }
  const targetFile = path.resolve(resolved.resolvedFileName);
  if (!isInside(targetFile, sourceRoot)) {
    return;
  }
  const source = normalizedId(sourceFile.fileName, sourceRoot);
  const target = normalizedId(targetFile, sourceRoot);
  dependencies.set(`${source}\u0000${target}`, { source, target });
}

for (const filename of files) {
  if (!isInside(filename, sourceRoot)) {
    errors.push(`source file is outside source root: ${filename}`);
    continue;
  }
  let text;
  try {
    text = fs.readFileSync(filename, "utf8");
  } catch (error) {
    errors.push(`cannot read ${filename}: ${error.message}`);
    continue;
  }
  const scriptKind = filename.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  const sourceFile = ts.createSourceFile(filename, text, options.target ?? ts.ScriptTarget.ES2022, true, scriptKind);
  for (const diagnostic of sourceFile.parseDiagnostics) {
    errors.push(
      `${normalizedId(filename, sourceRoot)}: TypeScript parse error: ${ts.flattenDiagnosticMessageText(diagnostic.messageText, " ")}`,
    );
  }

  function visit(node) {
    if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) && node.moduleSpecifier) {
      if (!ts.isStringLiteralLike(node.moduleSpecifier)) {
        errors.push(`${normalizedId(filename, sourceRoot)}: non-literal static module specifier`);
      } else {
        addSpecifier(sourceFile, node.moduleSpecifier.text);
      }
    } else if (ts.isImportEqualsDeclaration(node) && ts.isExternalModuleReference(node.moduleReference)) {
      const expression = node.moduleReference.expression;
      if (!expression || !ts.isStringLiteralLike(expression)) {
        errors.push(`${normalizedId(filename, sourceRoot)}: non-literal import-equals`);
      } else {
        addSpecifier(sourceFile, expression.text);
      }
    } else if (ts.isImportTypeNode(node)) {
      const argument = node.argument;
      if (!ts.isLiteralTypeNode(argument) || !ts.isStringLiteralLike(argument.literal)) {
        errors.push(`${normalizedId(filename, sourceRoot)}: non-literal import type`);
      } else {
        addSpecifier(sourceFile, argument.literal.text);
      }
    } else if (
      ts.isCallExpression(node) &&
      (node.expression.kind === ts.SyntaxKind.ImportKeyword || ts.isIdentifier(node.expression) && node.expression.text === "require")
    ) {
      const argument = node.arguments[0];
      if (!argument || !ts.isStringLiteralLike(argument)) {
        errors.push(`${normalizedId(filename, sourceRoot)}: unscannable non-literal dynamic import`);
      } else {
        addSpecifier(sourceFile, argument.text);
      }
    }
    ts.forEachChild(node, visit);
  }

  visit(sourceFile);
}

process.stdout.write(
  JSON.stringify({
    dependencies: [...dependencies.values()].sort((left, right) =>
      `${left.source}\u0000${left.target}`.localeCompare(`${right.source}\u0000${right.target}`),
    ),
    errors: [...new Set(errors)].sort(),
  }),
);
