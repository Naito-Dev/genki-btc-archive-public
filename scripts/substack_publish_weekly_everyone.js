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

function assertNoPlaceholders(raw) {
  const patterns = [
    /Days published:\s*X\s*\/\s*7/i,
    /Missing days:\s*Y\b/i,
    /Max delay:\s*N\s*sec/i,
    /VALID days:\s*V\s*\/\s*7/i,
    /week ending\s*YYYY-MM-DD/i,
    /last30_match_report_YYYY-MM-DD\.txt/i,
    /PASS\s*\/\s*FAIL\s*\/\s*unavailable/i,
  ];
  for (const re of patterns) {
    if (re.test(raw)) throw new Error('placeholder_not_resolved');
  }
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
  assertNoPlaceholders(raw);
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

  await page.waitForTimeout(5000);

  // Capture PUBLIC /p/ URL (do not accept /publish/post/).
  await page.goto('https://btcsignal.substack.com/publish/posts', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(2500);

  // Try to find a public link associated with the subject.
  let href = null;
  const subjectPublicLink = page.locator(`a[href*="/p/"]:has-text("${subject}")`).first();
  if ((await subjectPublicLink.count()) > 0) {
    href = await subjectPublicLink.getAttribute('href');
  }
  if (!href) {
    const anyP = page.locator('a[href*="/p/"]').first();
    if ((await anyP.count()) > 0) href = await anyP.getAttribute('href');
  }

  if (!href || !href.includes('/p/')) {
    await context.close();
    throw new Error('public_post_url_not_found');
  }

  const publicUrl = href.startsWith('http') ? href : `https://btcsignal.substack.com${href}`;
  if (!publicUrl.startsWith('https://btcsignal.substack.com/p/')) {
    await context.close();
    throw new Error('public_post_url_not_found');
  }

  await context.close();

  const nowUtc = new Date().toISOString();
  saveJson(LAST_PUBLISHED, {
    last_week_ending: (subject.match(/week ending (\d{4}-\d{2}-\d{2})/) || [null, null])[1],
    last_subject: subject,
    last_published_utc: nowUtc,
    last_post_url: publicUrl,
  });

  process.stdout.write(`OK: published subject=\"${subject}\" public_url=${publicUrl}\n`);
})().catch((e) => {
  process.stdout.write(`ERROR: ${e.message}\n`);
  process.exitCode = 1;
});
