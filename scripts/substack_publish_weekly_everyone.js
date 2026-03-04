#!/usr/bin/env node
/*
Weekly Substack publisher (Everyone + Send via email) using Playwright persistent profile.

- Generates weekly_latest.txt using scripts/generate_substack_weekly.py (ending = today UTC)
- Reads substack/weekly_latest.txt
- Dedupe: if subject matches substack/last_published_weekly.json.last_subject => NOOP
- Publishes via Substack UI (Create new -> Article -> Continue -> Everyone -> Send to everyone now)
*/

const { chromium } = require('playwright');
const { execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const USER_DATA_DIR = path.resolve(ROOT, '.runtime', 'pw-substack');

const WEEKLY_TXT = path.resolve(ROOT, 'substack', 'weekly_latest.txt');
const LAST_PUBLISHED = path.resolve(ROOT, 'substack', 'last_published_weekly.json');

function run(cmd, args) {
  return execFileSync(cmd, args, { encoding: 'utf8' });
}

function loadJson(p) {
  try { return JSON.parse(fs.readFileSync(p, 'utf8')); } catch { return {}; }
}

function saveJson(p, obj) {
  fs.writeFileSync(p, JSON.stringify(obj, null, 2) + '\n');
}

function formatCopyReady(raw) {
  const out = execFileSync('node', ['/Users/Claw/clawd/btcsignal_substack_format.js'], { input: raw, encoding: 'utf8' });
  const lines = out.replace(/\r\n/g, '\n').split('\n');
  const subject = (lines[0] || '').trim();
  const body = lines.slice(2).join('\n').trim();
  if (!subject || !body) throw new Error('format_failed');
  return { subject, body };
}

async function clickIfExists(locator) {
  if ((await locator.count()) > 0) {
    await locator.first().click({ force: true, timeout: 20000 });
    return true;
  }
  return false;
}

(async () => {
  // Generate weekly draft file (uses logs + live log)
  run('python3', [path.resolve(ROOT, 'scripts', 'generate_substack_weekly.py')]);

  const raw = fs.readFileSync(WEEKLY_TXT, 'utf8');
  const { subject, body } = formatCopyReady(raw);

  const prev = loadJson(LAST_PUBLISHED);
  if (prev.last_subject && prev.last_subject === subject) {
    process.stdout.write('NOOP: already_published\n');
    return;
  }

  const context = await chromium.launchPersistentContext(USER_DATA_DIR, {
    headless: false,
    viewport: { width: 1280, height: 800 },
  });
  const page = context.pages()[0] || (await context.newPage());

  await page.goto('https://btcsignal.substack.com/publish/home', { waitUntil: 'domcontentloaded' });

  if (page.url().includes('sign-in') || page.url().includes('login') || page.url().includes('account')) {
    await context.close();
    throw new Error('not_logged_in');
  }

  await page.getByRole('button', { name: /create new/i }).first().click({ timeout: 20000 });
  await page.getByRole('menuitem', { name: /article/i }).first().click({ timeout: 20000 });
  await page.waitForTimeout(2000);

  const titleBox = page.locator('textarea[aria-label="title" i], textarea[placeholder="Title" i]').first();
  await titleBox.waitFor({ timeout: 20000 });
  await titleBox.click({ force: true });
  await page.keyboard.press('Meta+A').catch(() => {});
  await page.keyboard.press('Control+A').catch(() => {});
  await page.keyboard.type(subject, { delay: 5 });

  let bodyBox = page.locator('div[contenteditable="true"][role="textbox"]:visible').first();
  if ((await bodyBox.count()) === 0) bodyBox = page.locator('[contenteditable="true"]:visible').last();
  await bodyBox.waitFor({ timeout: 20000 });
  await bodyBox.click({ force: true });
  await page.keyboard.press('Meta+A').catch(() => {});
  await page.keyboard.press('Control+A').catch(() => {});
  await page.keyboard.type(body, { delay: 2 });

  const continued = await clickIfExists(page.getByRole('button', { name: /continue/i }));
  if (!continued) {
    await context.close();
    throw new Error('continue_not_found');
  }

  await page.waitForTimeout(1500);
  await clickIfExists(page.getByRole('radio', { name: /^everyone$/i }));

  await page.waitForTimeout(800);
  const sendNow = page.getByRole('button', { name: /send to everyone now/i }).first();
  await sendNow.waitFor({ timeout: 20000 });
  await sendNow.click({ force: true });

  await page.waitForTimeout(6000);
  const url = page.url();
  await context.close();

  const nowUtc = new Date().toISOString();
  saveJson(LAST_PUBLISHED, {
    last_week_ending: (subject.match(/week ending (\d{4}-\d{2}-\d{2})/) || [null, null])[1],
    last_subject: subject,
    last_published_utc: nowUtc,
    last_post_url: url,
  });

  process.stdout.write(`OK: published subject=\"${subject}\" url=${url}\n`);
})().catch((e) => {
  process.stdout.write(`ERROR: ${e.message}\n`);
  process.exitCode = 1;
});
