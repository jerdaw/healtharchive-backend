#!/usr/bin/env node

/**
 * generate_replay_preview.js
 *
 * Used inside a Playwright Docker image to render a replay URL and save a
 * cached preview image (PNG).
 *
 * Usage (inside the container):
 *   node /ha-scripts/generate_replay_preview.js \
 *     --url "https://replay.healtharchive.ca/job-1/..." \
 *     --out "/out/source-hc-job-1.png" \
 *     --width 1000 --height 540 --timeout-ms 45000
 */

function argValue(flag, fallback = null) {
  const idx = process.argv.indexOf(flag);
  if (idx === -1) return fallback;
  if (idx + 1 >= process.argv.length) return fallback;
  return process.argv[idx + 1];
}

function intValue(flag, fallback) {
  const raw = argValue(flag);
  if (!raw) return fallback;
  const parsed = Number(raw);
  if (!Number.isFinite(parsed) || parsed <= 0) return fallback;
  return Math.trunc(parsed);
}

const url = argValue("--url");
const outPath = argValue("--out");
const width = intValue("--width", 1000);
const height = intValue("--height", 540);
const timeoutMs = intValue("--timeout-ms", 45000);
const settleMs = intValue("--settle-ms", 1200);

if (!url || !outPath) {
  // eslint-disable-next-line no-console
  console.error("Missing required args: --url and --out");
  process.exit(2);
}

async function main() {
  // Playwright is provided by the Playwright Docker image.
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const { chromium } = require("playwright");

  const browser = await chromium.launch({
    args: ["--disable-dev-shm-usage"],
  });

  const page = await browser.newPage({
    viewport: { width, height },
    deviceScaleFactor: 1,
  });
  page.setDefaultTimeout(timeoutMs);

  await page.goto(url, { waitUntil: "load", timeout: timeoutMs });
  await page.waitForTimeout(settleMs);

  // Safety: if any banner slipped in, remove it before taking the screenshot.
  try {
    await page.evaluate(() => {
      const banner = document.getElementById("ha-replay-banner");
      if (banner && banner.parentNode) banner.parentNode.removeChild(banner);
    });
  } catch {
    // Ignore JS evaluation failures.
  }

  await page.screenshot({ path: outPath, type: "png", fullPage: false });
  await browser.close();
}

main().catch((err) => {
  // eslint-disable-next-line no-console
  console.error(err);
  process.exit(1);
});

