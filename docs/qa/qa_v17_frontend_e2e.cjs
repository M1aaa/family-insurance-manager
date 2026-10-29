/**
 * v1.7 QA 独立验证 —— 真实浏览器 E2E（严过关 / Edward）。
 *
 * 用 Puppeteer 驱动真实 Chrome，独立验证：
 *   G1. 首页 6 张卡：桌面 3×2 / 平板 2×3 / 手机 2×3，任何视口无孤行；
 *   G2. 三视口 documentElement.scrollWidth <= clientWidth（无横向溢出）；
 *   G3. 卡片标签顺序正确；
 *   G4. 筛选面板：取消勾选 → 卡片实时变化；临时筛选期间 /api 请求数 = 0；刷新即重置；
 *   G5. 「存为默认」→ 硬刷新后仍生效；
 *   G6. 成员表单已无「计入家庭保费统计」开关；成员列表为只读「统计状态」；
 *   G7. 手机（390）筛选面板可用、不溢出；
 *   D.  端到端：设为不计入 → 通过 UI 编辑该成员 → 仍为不计入、看板未被改回。
 *
 * 运行：
 *   NODE_PATH=<工具目录>/node_modules node docs/qa/qa_v17_frontend_e2e.cjs
 * 浏览器工具链装在仓库之外（独立工具目录，可用 `QA_BROWSER_DIR` 指定），本仓库不产生 node_modules。
 */
const path = require('path');
const fs = require('fs');
const os = require('os');
const { spawn } = require('child_process');

let puppeteer;
try {
  puppeteer = require('puppeteer');
} catch (e) {
  console.error('无法加载 puppeteer：', e.message);
  process.exit(3);
}

const PROJECT = path.resolve(__dirname, '..', '..');
const PY = path.join(PROJECT, '.venv', 'Scripts', 'python.exe');
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const SHOT_DIR = path.join(PROJECT, 'docs', 'screenshots', 'v17-qa');
const PORT = 8017;
const BASE = `http://127.0.0.1:${PORT}`;

const R = { assertions: [], consoleErrors: [], pageErrors: [], requestFailures: [], notes: [], screenshots: [] };
function assert(name, cond, extra) {
  R.assertions.push({ name, pass: !!cond, extra: extra === undefined ? null : extra });
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function api(method, p, body) {
  const res = await fetch(BASE + p, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (e) { data = text; }
  return { status: res.status, data };
}

async function waitHealth(timeoutMs = 40000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    try {
      const r = await api('GET', '/api/health');
      if (r.status === 200) return r.data;
    } catch (e) { /* retry */ }
    await sleep(400);
  }
  return null;
}

/** 从「¥12,000.00」解析出 12000 */
function parseMoney(text) {
  const m = String(text).replace(/[^\d.-]/g, '');
  return m ? Number(m) : 0;
}

async function main() {
  fs.mkdirSync(SHOT_DIR, { recursive: true });
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'qa_v17_'));
  const env = Object.assign({}, process.env, {
    DB_PATH: path.join(tmp, 'insurance.db'),
    ATTACHMENTS_DIR: path.join(tmp, 'attachments'),
    PORT: String(PORT),
    PYTHONIOENCODING: 'utf-8',
  });
  const logFd = fs.openSync(path.join(tmp, 'uvicorn.log'), 'w');
  const proc = spawn(PY, ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(PORT)],
    { cwd: PROJECT, env, stdio: ['ignore', logFd, logFd] });

  let browser = null;
  try {
    const health = await waitHealth();
    if (!health) {
      R.notes.push('uvicorn 未就绪');
      R.notes.push(fs.readFileSync(path.join(tmp, 'uvicorn.log'), 'utf-8').slice(-3000));
      throw new Error('server not ready');
    }
    assert('版本号为 1.7.0', health.version === '1.7.0', { version: health.version });

    // ---- 预置数据（经真实 HTTP）----
    async function mkMember(name, relation, birthDate, gender) {
      return (await api('POST', '/api/members', { name, relation, birthDate, gender })).data;
    }
    const m1 = await mkMember('张三', '本人', '1988-06-01', '男');
    const m2 = await mkMember('李四', '配偶', '1990-03-12', '女');
    const m3 = await mkMember('小张', '子女', '2015-09-20', '男');
    const m4 = await mkMember('王五', '父亲', '1960-02-10', '男');

    async function mkPolicy(over) {
      const payload = Object.assign({
        company: '测试', insuranceType: '重疾险', productName: 'P',
        policyholderId: m1.id, insuredId: m1.id, effectiveDate: '2020-03-15',
        coverageTermType: '终身', coverageTermValue: null,
        paymentTermType: '固定年限', paymentTermValue: 20, paymentFrequency: '月缴',
        premiumPerPeriod: 500, status: '生效中',
      }, over);
      return (await api('POST', '/api/policies', payload)).data;
    }
    // p_A 6000 / p_B 8000 / p_C 3000 / p_D 0（趸交已缴清）/ p_E 12000（王五）
    await mkPolicy({ id: 'p_A', insuranceType: '重疾险', productName: 'A', policyholderId: m1.id, insuredId: m1.id, paymentFrequency: '月缴', premiumPerPeriod: 500 });
    await mkPolicy({ insuranceType: '医疗险', productName: 'B', policyholderId: m1.id, insuredId: m2.id, effectiveDate: '2021-08-01', paymentFrequency: '年缴', premiumPerPeriod: 8000, paymentTermValue: 10 });
    await mkPolicy({ insuranceType: '意外险', productName: 'C', policyholderId: m1.id, insuredId: m3.id, effectiveDate: '2024-06-01', paymentFrequency: '年缴', premiumPerPeriod: 3000 });
    await mkPolicy({ insuranceType: '重疾险', productName: 'D', policyholderId: m1.id, insuredId: m1.id, effectiveDate: '2019-01-01', paymentTermType: '趸交', paymentTermValue: null, premiumPerPeriod: 50000, status: '已缴清' });
    await mkPolicy({ insuranceType: '寿险', productName: 'E', policyholderId: m4.id, insuredId: m4.id, effectiveDate: '2015-03-01', coverageTermType: '终身', paymentTermType: '交至某年龄', paymentTermValue: 60, paymentFrequency: '年缴', premiumPerPeriod: 12000 });
    R.notes.push('seeded 4 members / 5 policies');

    // 基线（默认全部计入）
    const dash0 = (await api('GET', '/api/stats/dashboard')).data;
    const baseAnnual = dash0.overview.annualTotalPremium;      // 期望 29000
    const baseYearUnpaid = dash0.overview.yearUnpaidPremium;   // 记录，筛选期间不得变
    assert('基线 annualTotalPremium = 29000', baseAnnual === 29000, { baseAnnual });

    // ---- 启动浏览器 ----
    browser = await puppeteer.launch({
      headless: 'shell',
      executablePath: fs.existsSync(CHROME) ? CHROME : undefined,
      args: ['--no-sandbox', '--disable-setuid-sandbox', '--no-proxy-server', '--disable-dev-shm-usage'],
    });
    const page = await browser.newPage();
    let reqCount = 0;
    let reqLog = [];
    page.on('console', (msg) => { if (msg.type() === 'error') R.consoleErrors.push(msg.text()); });
    page.on('pageerror', (err) => R.pageErrors.push(String(err && err.message ? err.message : err)));
    page.on('request', (req) => { reqCount += 1; reqLog.push(req.url()); });
    page.on('requestfailed', (req) => {
      R.requestFailures.push({ url: req.url(), err: req.failure() && req.failure().errorText });
    });

    const cardsInfo = () => page.evaluate(() => {
      const els = Array.from(document.querySelectorAll('.metric-card'));
      const labels = els.map((e) => (e.querySelector('.metric-label') || {}).textContent || '');
      const values = els.map((e) => (e.querySelector('.metric-value') || {}).textContent || '');
      const tops = els.map((e) => Math.round(e.getBoundingClientRect().top));
      const uniq = Array.from(new Set(tops)).sort((a, b) => a - b);
      const rows = uniq.map((t) => tops.filter((x) => x === t).length);
      return { count: els.length, labels, values, rows };
    });
    const overflow = () => page.evaluate(() => ({
      sw: document.documentElement.scrollWidth,
      cw: document.documentElement.clientWidth,
    }));

    // ============ G1/G2/G3：三视口布局 ============
    const viewports = [
      { w: 1440, h: 900, expectRows: [3, 3], name: '1440x900' },
      { w: 768, h: 1024, expectRows: [2, 2, 2], name: '768x1024' },
      { w: 390, h: 844, expectRows: [2, 2, 2], name: '390x844' },
    ];
    for (const vp of viewports) {
      await page.setViewport({ width: vp.w, height: vp.h });
      await page.goto(`${BASE}/#/dashboard`, { waitUntil: 'networkidle2' });
      await page.waitForSelector('.metric-card', { timeout: 10000 });
      await sleep(400);
      const info = await cardsInfo();
      assert(`[${vp.name}] 卡片数 = 6`, info.count === 6, { count: info.count });
      assert(`[${vp.name}] 行分布 = ${JSON.stringify(vp.expectRows)}（无孤行）`,
        JSON.stringify(info.rows) === JSON.stringify(vp.expectRows), { rows: info.rows });
      const ov = await overflow();
      assert(`[${vp.name}] 无横向溢出`, ov.sw <= ov.cw, ov);
      const shot = path.join(SHOT_DIR, `dashboard-${vp.name}.png`);
      await page.screenshot({ path: shot, fullPage: true });
      R.screenshots.push(shot);
    }

    // G3：卡片标签顺序（用 1440 视口）
    await page.setViewport({ width: 1440, height: 900 });
    await page.goto(`${BASE}/#/dashboard`, { waitUntil: 'networkidle2' });
    await page.waitForSelector('.metric-card', { timeout: 10000 });
    await sleep(300);
    const info1440 = await cardsInfo();
    const expectLabels = ['当年应缴总保费', '年化保费合计', '当年未缴费金额', '近 60 天待缴', '保单总数', '在保人数'];
    assert('卡片标签顺序正确（②改名年化保费合计 / ③新增当年未缴费金额）',
      JSON.stringify(info1440.labels) === JSON.stringify(expectLabels), { labels: info1440.labels });

    // ============ G4：临时筛选（无请求 + 实时变化 + 刷新重置） ============
    const cardIdx = (labels, name) => labels.indexOf(name);
    const i1 = cardIdx(info1440.labels, '当年应缴总保费');
    const i3 = cardIdx(info1440.labels, '当年未缴费金额');
    assert('基线卡片① = 29000', parseMoney(info1440.values[i1]) === 29000, { v: info1440.values[i1] });

    // 找到张三(m1) 的 holder 复选框并取消勾选
    const holderSel = `#filter-holders input[data-id="${m1.id}"]`;
    await page.waitForSelector(holderSel, { timeout: 8000 });
    reqCount = 0; reqLog = [];
    await page.click(holderSel);           // 取消勾选 = 临时排除
    await sleep(600);
    const apiReqs = reqLog.filter((u) => u.includes('/api/'));
    assert('临时筛选期间 /api 请求数 = 0', apiReqs.length === 0, { apiReqs });
    const infoF = await cardsInfo();
    assert('取消勾选后卡片① 实时变为 12000', parseMoney(infoF.values[i1]) === 12000, { v: infoF.values[i1] });
    assert('卡片③（当年未缴费金额）不受筛选影响',
      parseMoney(infoF.values[i3]) === baseYearUnpaid, { before: baseYearUnpaid, after: parseMoney(infoF.values[i3]) });

    // 再取消一个险种（重疾险）→ 并集，p_E 仍为 12000（p_A 命中两维只扣一次）
    await page.click('#filter-types input[data-id="重疾险"]');
    await sleep(500);
    const infoF2 = await cardsInfo();
    assert('并集（张三∪重疾险）无重复扣减 → 卡片① 仍 12000',
      parseMoney(infoF2.values[i1]) === 12000, { v: infoF2.values[i1] });

    // 刷新即重置（临时筛选不落库）
    await page.reload({ waitUntil: 'networkidle2' });
    await page.waitForSelector('.metric-card', { timeout: 10000 });
    await sleep(500);
    const infoReload = await cardsInfo();
    assert('刷新后临时筛选重置 → 卡片① 回到 29000',
      parseMoney(infoReload.values[i1]) === 29000, { v: infoReload.values[i1] });
    const m1checked = await page.$eval(holderSel, (c) => c.checked);
    assert('刷新后张三复选框恢复勾选', m1checked === true);

    // ============ G5：存为默认 → 硬刷新后仍生效 ============
    await page.click(holderSel);   // 取消勾选
    await sleep(300);
    await page.click('#btn-save-filter');
    await sleep(900);
    const exAfterSave = (await api('GET', '/api/stats/exclusions')).data;
    assert('「存为默认」写入服务端（含张三 id）',
      exAfterSave.excludedPolicyholderIds.includes(m1.id), exAfterSave);

    await page.reload({ waitUntil: 'networkidle2' });
    await page.waitForSelector('.metric-card', { timeout: 10000 });
    await sleep(600);
    const infoSaved = await cardsInfo();
    assert('硬刷新后「存为默认」仍生效 → 卡片① = 12000',
      parseMoney(infoSaved.values[i1]) === 12000, { v: infoSaved.values[i1] });
    const m1checkedSaved = await page.$eval(holderSel, (c) => c.checked);
    assert('硬刷新后张三复选框仍为未勾选', m1checkedSaved === false);

    // ============ G6：成员页表单无开关 + 只读统计状态 ============
    await page.goto(`${BASE}/#/members`, { waitUntil: 'networkidle2' });
    await page.waitForSelector('#view-members .data-table', { timeout: 10000 });
    await sleep(300);
    const hasStatHeader = await page.evaluate(() => {
      const ths = Array.from(document.querySelectorAll('#view-members .data-table thead th'));
      return ths.some((t) => t.textContent.trim() === '统计状态');
    });
    assert('成员列表表头含「统计状态」', hasStatHeader);
    const rowBadges = await page.$$eval('#view-members .data-table tbody tr', (rows) =>
      rows.map((r) => (r.querySelector('td:last-child') ? '' : ''))
    );
    // 张三行应显示「不计入」
    const zhangsanBadge = await page.evaluate((mid) => {
      const rows = Array.from(document.querySelectorAll('#view-members .data-table tbody tr'));
      const row = rows.find((r) => r.innerHTML.includes(mid));
      if (!row) return null;
      const badge = row.querySelector('.badge');
      return badge ? badge.textContent.trim() : null;
    }, m1.id);
    assert('张三行统计状态 = 不计入', zhangsanBadge === '不计入', { zhangsanBadge });

    // 打开编辑弹窗，确认无「计入家庭保费统计」开关
    await page.click(`[data-action="edit"][data-id="${m1.id}"]`);
    await page.waitForSelector('.modal .form-grid', { timeout: 8000 });
    await sleep(300);
    const formText = await page.$eval('.modal .form-grid', (el) => el.textContent);
    const hasCheckbox = await page.evaluate(() =>
      !!document.querySelector('.modal input[name="countInStats"], .modal input[name="count_in_stats"]'));
    assert('成员表单无「计入家庭保费统计」开关', !formText.includes('计入家庭保费统计') && !hasCheckbox);

    // ============ D：编辑成员不得重置「不计入」 ============
    // 在弹窗里改备注后保存
    await page.$eval('.modal input[name="remark"]', (el) => { el.value = ''; });
    await page.type('.modal input[name="remark"]', 'QA改过备注');
    await page.click('.modal-footer button.btn.primary');
    await sleep(1000);
    const exAfterEdit = (await api('GET', '/api/stats/exclusions')).data;
    assert('★ 编辑成员后仍为「不计入」（未被重置）',
      exAfterEdit.excludedPolicyholderIds.includes(m1.id), exAfterEdit);
    const m1after = (await api('GET', '/api/members')).data.find((m) => m.id === m1.id);
    assert('★ 编辑后 countInStats 仍为 false', m1after.countInStats === false, { c: m1after.countInStats });
    assert('备注已落库（确认编辑确实生效）', m1after.remark === 'QA改过备注', { r: m1after.remark });

    // 看板数字未被悄悄改回
    await page.goto(`${BASE}/#/dashboard`, { waitUntil: 'networkidle2' });
    await page.waitForSelector('.metric-card', { timeout: 10000 });
    await sleep(500);
    const infoAfterEdit = await cardsInfo();
    assert('★ 编辑成员后看板卡片① 仍为 12000（未被改回）',
      parseMoney(infoAfterEdit.values[i1]) === 12000, { v: infoAfterEdit.values[i1] });
    const shotMembers = path.join(SHOT_DIR, 'members-390x844.png');
    await page.setViewport({ width: 390, height: 844 });
    await page.goto(`${BASE}/#/members`, { waitUntil: 'networkidle2' });
    await page.waitForSelector('#view-members .data-table', { timeout: 10000 });
    await sleep(400);
    await page.screenshot({ path: shotMembers, fullPage: true });
    R.screenshots.push(shotMembers);

    // ============ G7：手机筛选面板可用、不溢出 ============
    await page.setViewport({ width: 390, height: 844 });
    await page.goto(`${BASE}/#/dashboard`, { waitUntil: 'networkidle2' });
    await page.waitForSelector('.filter-panel', { timeout: 10000 });
    await sleep(400);
    const panelOk = await page.evaluate(() => {
      const panel = document.querySelector('.filter-panel');
      if (!panel) return { ok: false };
      const chips = panel.querySelectorAll('.filter-chip').length;
      const r = panel.getBoundingClientRect();
      return { ok: true, chips, width: r.width, cw: document.documentElement.clientWidth };
    });
    assert('手机端筛选面板存在且有勾选项', panelOk.ok && panelOk.chips >= 5, panelOk);
    const ovMobile = await overflow();
    assert('手机端筛选面板不横向溢出', ovMobile.sw <= ovMobile.cw, ovMobile);
    // 手机上点一次筛选仍可用
    const before = await cardsInfo();
    await page.click(`#filter-types input[data-id="寿险"]`); // 再排寿险（此时张三已默认排除）
    await sleep(500);
    const afterMobile = await cardsInfo();
    assert('手机端点击筛选生效（卡片① 变化）',
      parseMoney(afterMobile.values[i1]) !== parseMoney(before.values[i1]),
      { before: before.values[i1], after: afterMobile.values[i1] });
    const shotMobileFilter = path.join(SHOT_DIR, 'dashboard-390x844-filtered.png');
    await page.screenshot({ path: shotMobileFilter, fullPage: true });
    R.screenshots.push(shotMobileFilter);

    // ============ 无 JS 异常 ============
    assert('无 pageerror（未捕获异常）', R.pageErrors.length === 0, { errors: R.pageErrors });
    assert('无 console.error', R.consoleErrors.length === 0, { errors: R.consoleErrors });
    const realFailures = R.requestFailures.filter((f) => f.err !== 'net::ERR_ABORTED');
    assert('无真实请求失败', realFailures.length === 0, { realFailures });

  } catch (err) {
    R.notes.push('EXCEPTION: ' + (err && err.stack ? err.stack : String(err)));
  } finally {
    if (browser) await browser.close().catch(() => {});
    proc.kill();
    try { fs.closeSync(logFd); } catch (e) { /* ignore */ }
  }

  const passed = R.assertions.filter((a) => a.pass).length;
  R.summary = { total: R.assertions.length, passed, failed: R.assertions.length - passed };
  const out = path.join(os.tmpdir(), 'qa_v17_e2e_out.json');
  fs.writeFileSync(out, JSON.stringify(R, null, 2));
  console.log(JSON.stringify(R.summary));
  process.exit(R.summary.failed === 0 && !R.notes.some((n) => n.startsWith('EXCEPTION')) ? 0 : 1);
}

main();
