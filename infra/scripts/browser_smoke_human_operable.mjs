#!/usr/bin/env node
import { existsSync } from "node:fs";
import { createRequire } from "node:module";

const require = createRequire(new URL("../../web/package.json", import.meta.url));
const { chromium } = require("playwright-core");

const webUrl = process.env.ATLAS_PRODUCTION_WEB_URL ?? "http://127.0.0.1:5184";
const providerEndpoint = process.env.ATLAS_PRODUCTION_PROVIDER_ENDPOINT ?? "https://api.openai.com/v1";
const providerApiKey = process.env.ATLAS_PRODUCTION_SMOKE_PROVIDER_API_KEY;
const providerModel = process.env.ATLAS_PRODUCTION_PROVIDER_MODEL ?? "gpt-4.1-mini";
const smokeAdminEmail = process.env.ATLAS_HUMAN_SMOKE_ADMIN_EMAIL;
const smokeAdminPassword = process.env.ATLAS_HUMAN_SMOKE_ADMIN_PASSWORD;
if (!providerApiKey) {
  throw new Error(
    "ATLAS_PRODUCTION_SMOKE_PROVIDER_API_KEY is required and is entered through the admin UI.",
  );
}
if (!smokeAdminEmail || !smokeAdminPassword) {
  throw new Error(
    "ATLAS_HUMAN_SMOKE_ADMIN_EMAIL and ATLAS_HUMAN_SMOKE_ADMIN_PASSWORD are required.",
  );
}
const screenshotPath =
  process.env.ATLAS_PRODUCTION_BROWSER_SMOKE_SCREENSHOT ??
  "/tmp/atlas-production-p0-browser-smoke.png";

const chromeCandidates = [
  process.env.CHROME_EXECUTABLE_PATH,
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
  "/usr/bin/chromium-browser",
].filter(Boolean);

const executablePath = chromeCandidates.find((path) => existsSync(path));
if (!executablePath) {
  throw new Error(
    "No local Chromium-compatible browser found. Set CHROME_EXECUTABLE_PATH to run the browser smoke.",
  );
}

const browser = await chromium.launch({
  executablePath,
  headless: true,
});

try {
  const context = await browser.newContext();
  await context.addInitScript(() => {
    window.localStorage.setItem("atlas.production.language", "en");
  });
  const page = await context.newPage();
  page.setDefaultTimeout(10_000);
  const runId = Date.now().toString(36);
  const engineerName = `Smoke Engineer ${runId}`;
  const engineerEmail = `engineer+${runId}@example.test`;
  const engineerPassword = "AtlasLocalEngineer!01";
  const projectName = `Smoke Project ${runId}`;
  const connectionName = `Smoke Provider ${runId}`;
  const routeName = `Smoke Model ${runId}`;
  const documentTitle = `Smoke Layout Guideline ${runId}`;

  await page.goto(`${webUrl}/login`);
  await page
    .getByRole("heading", { name: "Create the first administrator" })
    .waitFor();
  await page.getByLabel("Display name").fill("Atlas Smoke Administrator");
  await page.getByLabel("Email").fill(smokeAdminEmail);
  await page.getByLabel("Password").fill(smokeAdminPassword);
  await page.getByLabel("Confirm password").fill(smokeAdminPassword);
  await page.getByRole("button", { name: "Create administrator" }).click();

  await page
    .getByRole("heading", { name: "Connect a model provider" })
    .waitFor();
  await page.getByLabel("Connection name").fill(connectionName);
  await page.getByLabel("Endpoint URL").fill(providerEndpoint);
  await page.getByLabel("API key").fill(providerApiKey);
  await page
    .getByRole("button", { name: "Test connection and find models" })
    .click();
  await page.getByLabel("Text model").waitFor();
  const textModelCombobox = page.getByRole("combobox", { name: "Text model" });
  if ((await textModelCombobox.count()) > 0) {
    await textModelCombobox.click();
    await page
      .getByRole("option", { name: providerModel, exact: true })
      .click();
  } else {
    await page.getByLabel("Text model").fill(providerModel);
  }
  await page
    .getByRole("button", { name: "Test and use this text model" })
    .click();

  await page
    .getByRole("heading", { name: "Create or choose a project" })
    .waitFor();
  await page.getByLabel("New project name").fill(projectName);
  await page.getByRole("button", { name: "Create project" }).click();

  await page.getByRole("heading", { name: "Add the first document" }).waitFor();
  await page
    .getByLabel("Document file")
    .setInputFiles({
      name: `${documentTitle}.pdf`,
      mimeType: "application/pdf",
      buffer: searchablePdf(
        "The synthetic reference target is documented in the example source differential, with tolerance set by the project stackup note.",
      ),
    });
  await page.getByRole("button", { name: "Upload document" }).click();

  await page
    .getByRole("heading", { name: "Review Atlas readiness" })
    .waitFor();
  await page.getByRole("button", { name: "Enter Atlas" }).click();
  await page.getByRole("heading", { name: "Workspace" }).waitFor();

  await page.getByRole("button", { name: "Document Library" }).click();
  await page.getByRole("heading", { name: "Document Library" }).waitFor();
  await page.getByLabel("Target").click();
  await page
    .getByRole("option", {
      name: new RegExp(`Project: ${escapeRegExp(projectName)}`),
    })
    .click();
  const documentRow = page.getByRole("row").filter({ hasText: documentTitle });
  await documentRow.getByRole("button", { name: "Manage" }).click();
  await page.getByText("Ready", { exact: true }).waitFor({ timeout: 120_000 });
  await page.keyboard.press("Escape");

  await page.getByRole("button", { name: "Users" }).click();
  await page.getByRole("heading", { name: "Users" }).waitFor();
  await page.getByRole("button", { name: /create invite/i }).click();
  await page.getByLabel("Member name").fill(engineerName);
  await page.getByLabel("Member email").fill(engineerEmail);
  await page
    .getByRole("dialog")
    .getByRole("button", { name: /create invite/i })
    .click();
  await page.getByText(/Invite is ready/).first().waitFor();
  const inviteLink = await page.getByLabel("Invite acceptance link").inputValue();
  await page.getByRole("dialog").getByRole("button", { name: /cancel/i }).click();
  await page.getByRole("button", { name: /sign out/i }).click();
  await page.goto(new URL(inviteLink, webUrl).toString());
  await page.getByRole("heading", { name: "Accept invite" }).waitFor();
  await page.getByLabel("New password").fill(engineerPassword);
  await page.getByLabel("Confirm password").fill(engineerPassword);
  await page.getByRole("button", { name: /accept invite/i }).click();
  await page.getByText(/Your account is active/).waitFor();
  await page.getByRole("button", { name: /go to sign in/i }).click();
  await page.getByRole("heading", { name: "Atlas Production" }).waitFor();
  await page.getByLabel("Email").fill(smokeAdminEmail);
  await page.getByLabel("Password").fill(smokeAdminPassword);
  await page.getByRole("button", { name: /sign in/i }).click();
  await page.getByRole("heading", { name: "Workspace" }).waitFor();
  await page.getByRole("button", { name: "Projects" }).click();
  await page.getByRole("heading", { name: "Projects" }).waitFor();
  await page.getByRole("button", { name: projectName }).click();
  await page.getByRole("tab", { name: "Members" }).click();
  await page.getByLabel("Add existing user").click();
  await page.getByPlaceholder("Search users").fill(engineerName);
  await page.getByRole("option", { name: new RegExp(escapeRegExp(engineerName)) }).click();
  await page.getByRole("button", { name: "Add member" }).first().click();
  await page.getByText(/Project member is active/).first().waitFor();
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: /sign out/i }).click();
  await page.getByRole("heading", { name: "Atlas Production" }).waitFor();
  await page.getByLabel("Email").fill(engineerEmail);
  await page.getByLabel("Password").fill(engineerPassword);
  await page.getByRole("button", { name: /sign in/i }).click();
  await page.getByRole("heading", { name: "Workspace" }).waitFor();
  await page
    .getByLabel("Message")
    .fill("What is the approved value for the selected item?");
  const turnResponsePromise = page.waitForResponse(
    (response) =>
      response.url().includes("/api/v1/workspace/conversations/") &&
      response.url().endsWith("/turns") &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: /^Send$/ }).click();
  const turnResponse = await turnResponsePromise;
  const acceptedTurn = await turnResponse.json();
  if (!turnResponse.ok() || typeof acceptedTurn.execution_id !== "string") {
    throw new Error(`Provider-backed conversation turn was not accepted: ${JSON.stringify(acceptedTurn)}`);
  }
  const terminalResponse = await page.waitForResponse(
    (response) =>
      response.url().endsWith(`/api/v1/workspace/turn-executions/${acceptedTurn.execution_id}`) &&
      response.request().method() === "GET",
  );
  const terminalStatus = await terminalResponse.json();
  if (!["terminal_completed", "terminal_failed"].includes(terminalStatus.state)) {
    throw new Error(`Provider-backed execution did not terminalize: ${JSON.stringify(terminalStatus)}`);
  }
  await page
    .getByLabel("Message")
    .fill("What is the full board-level root cause of the intermittent boot failure?");
  await page.getByRole("button", { name: /^Send$/ }).click();
  await page.getByText(/cannot answer this claim/i).waitFor();

  await page.getByRole("button", { name: /sign out/i }).click();
  await page.getByRole("heading", { name: "Atlas Production" }).waitFor();
  await page.getByLabel("Email").fill(smokeAdminEmail);
  await page.getByLabel("Password").fill(smokeAdminPassword);
  await page.getByRole("button", { name: /sign in/i }).click();
  await page.getByRole("heading", { name: "Workspace" }).waitFor();
  await page.getByRole("button", { name: "Projects" }).click();
  await page.getByRole("heading", { name: "Projects" }).waitFor();
  await page.getByRole("button", { name: projectName }).click();
  await page.getByRole("tab", { name: "Members" }).click();
  const grantRow = page.getByRole("row").filter({ hasText: engineerName });
  await grantRow.getByRole("button", { name: new RegExp(`remove ${escapeRegExp(engineerName)}`, "i") }).click();
  const revokeDialog = page.getByRole("alertdialog");
  await revokeDialog.getByRole("button", { name: /^Remove$/ }).click();
  await page.getByText(/Project member access is revoked/).first().waitFor();
  await page.keyboard.press("Escape");

  await page.getByRole("button", { name: /sign out/i }).click();
  await page.getByRole("heading", { name: "Atlas Production" }).waitFor();
  await page.getByLabel("Email").fill(engineerEmail);
  await page.getByLabel("Password").fill(engineerPassword);
  await page.getByRole("button", { name: /sign in/i }).click();
  await page.getByRole("heading", { name: "Workspace" }).waitFor();
  await page
    .getByLabel("Message")
    .fill("What is the approved value for the selected item?");
  await page.getByRole("button", { name: /^Send$/ }).click();
  await page.getByText(/do not currently have access/i).first().waitFor();

  await page.screenshot({ path: screenshotPath, fullPage: true });
  console.log(`browser smoke passed: ${screenshotPath}`);
} finally {
  await browser.close();
}

function searchablePdf(text) {
  const stream = Buffer.from(`BT /F1 12 Tf 72 720 Td (${escapePdfText(text)}) Tj ET`, "utf8");
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> /MediaBox [0 0 612 792] /Contents 5 0 R >>",
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    `<< /Length ${stream.length} >>\nstream\n${stream.toString("utf8")}\nendstream`,
  ];
  const chunks = [Buffer.from("%PDF-1.4\n", "ascii")];
  const offsets = [0];
  for (const [index, object] of objects.entries()) {
    offsets.push(Buffer.concat(chunks).length);
    chunks.push(Buffer.from(`${index + 1} 0 obj\n${object}\nendobj\n`, "utf8"));
  }
  const body = Buffer.concat(chunks);
  const xrefAt = body.length;
  const xref = [
    `xref\n0 ${objects.length + 1}\n`,
    "0000000000 65535 f \n",
    ...offsets.slice(1).map((offset) => `${String(offset).padStart(10, "0")} 00000 n \n`),
    `trailer << /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xrefAt}\n%%EOF\n`,
  ].join("");
  return Buffer.concat([body, Buffer.from(xref, "ascii")]);
}

function escapePdfText(text) {
  return text.replaceAll("\\", "\\\\").replaceAll("(", "\\(").replaceAll(")", "\\)");
}

function escapeRegExp(text) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
