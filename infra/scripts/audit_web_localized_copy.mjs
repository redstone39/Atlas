#!/usr/bin/env node

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import ts from "../../web/node_modules/typescript/lib/typescript.js";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const sourceRoot = path.resolve(scriptDirectory, "../../web/src");
const contractPath = path.resolve(scriptDirectory, "../../contracts/user-messages.json");
const visibleStringAttributes = new Set([
  "alt",
  "aria-label",
  "aria-roledescription",
  "label",
  "placeholder",
  "title",
]);
const findings = [];

function localizedMessageCodes(localeFile) {
  const sourceText = fs.readFileSync(localeFile, "utf8");
  return new Set(
    [...sourceText.matchAll(/^\s*"messages\.([^"]+)"\s*:/gm)].map((match) => match[1]),
  );
}

function inspectMessageCoverage() {
  const contract = JSON.parse(fs.readFileSync(contractPath, "utf8"));
  const catalogCodes = new Set(Object.keys(contract.messages ?? {}));
  const localeFiles = {
    en: path.join(sourceRoot, "locales/en.ts"),
    "zh-TW": path.join(sourceRoot, "locales/zh-TW.ts"),
  };

  for (const [locale, localeFile] of Object.entries(localeFiles)) {
    const localizedCodes = localizedMessageCodes(localeFile);
    for (const code of catalogCodes) {
      if (!localizedCodes.has(code)) findings.push(`${locale} is missing message code: ${code}`);
    }
    for (const code of localizedCodes) {
      if (!catalogCodes.has(code)) findings.push(`${locale} has unknown message code: ${code}`);
    }
  }
}

function inspectSource(filePath, sourceText, destination = findings) {
  const sourceFile = ts.createSourceFile(
    filePath,
    sourceText,
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.TSX,
  );

  function record(node, kind, value) {
    const line = sourceFile.getLineAndCharacterOfPosition(node.pos).line + 1;
    destination.push(`${path.relative(sourceRoot, filePath)}:${line} ${kind}: ${JSON.stringify(value)}`);
  }

  function visit(node) {
    if (ts.isJsxText(node)) {
      const value = node.getText(sourceFile).trim();
      if (/[A-Za-z\u3400-\u9fff]/.test(value)) record(node, "visible JSX text", value);
    }
    if (
      ts.isJsxAttribute(node) &&
      visibleStringAttributes.has(node.name.text) &&
      node.initializer &&
      ts.isStringLiteral(node.initializer) &&
      /[A-Za-z\u3400-\u9fff]/.test(node.initializer.text)
    ) {
      record(node, `literal ${node.name.text}`, node.initializer.text);
    }
    ts.forEachChild(node, visit);
  }

  visit(sourceFile);
}

function inspectFile(filePath) {
  inspectSource(filePath, fs.readFileSync(filePath, "utf8"));
}

function verifyVisibleAttributeRules() {
  const fixtureFindings = [];
  inspectSource(
    path.join(sourceRoot, "__localized_copy_audit_fixture__.tsx"),
    '<Widget label="Visible field" /><div aria-roledescription="carousel" />',
    fixtureFindings,
  );
  if (fixtureFindings.length !== 2) {
    throw new Error("Localized copy audit self-check failed for label or aria-roledescription.");
  }
}

function walk(directory) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const filePath = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      walk(filePath);
    } else if (entry.name.endsWith(".tsx") && !entry.name.includes(".test.")) {
      inspectFile(filePath);
    }
  }
}

verifyVisibleAttributeRules();
walk(sourceRoot);
inspectMessageCoverage();

if (findings.length > 0) {
  console.error("Localized copy audit failed. Move visible product copy into the locale resources:");
  console.error(findings.join("\n"));
  process.exit(1);
}

console.log("Localized copy audit passed: visible TSX prose and user-message locale coverage are consistent.");
