/**
 * QA 阶段 B —— B3 前端真实浏览器 E2E（独立断言，不照抄工程师用例）。
 *
 * 用 Puppeteer 驱动真实 Chrome，跑通 v1.2 三块功能的浏览器侧交互：
 *   ① 批量编辑三态（留空=不改 / 设值 / 高级=设为空）
 *   ② 编辑弹窗 collect() 7 字段落库
 *   ③ 三入口「常用值」datalist 提示（顶栏新增 / 列表新增 / 编辑弹窗）
 *   ④ 勾选状态跨重渲染保持
 *   ⑤ 附件全流程（上传 / 列表 / 查看 / 下载 / 删除）
 *   ⑥ 全程无 JS 异常、无 console.error、无请求失败
 *   ⑦ 仪表盘 / 成员视图回归
 *
 * 运行：NODE_PATH=<工具目录>/node_modules node docs/qa/qa_b3_frontend_e2e.cjs
 * 浏览器工具链装在**仓库之外**（独立工具目录，可用 `QA_BROWSER_DIR` 指定），本仓库不产生 node_modules。
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

const PORT = 8012;
const BASE = `http://127.0.0.1:${PORT}`;
const SIM = process.env.QA_SIM_DIR || path.join(os.tmpdir(), '_qa_build_sim');
const PY = path.join(SIM, '.venv', 'Scripts', 'python.exe');

const R = { assertions: [], consoleErrors: [], pageErrors: [], requestFailures: [], notes: [] };
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

async function waitHealth(timeoutMs = 30000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    try {
      const r = await api('GET', '/api/health');
      if (r.status === 200) return true;
    } catch (e) { /* retry */ }
    await sleep(400);
  }
  return false;
}

async function main() {
  if (!fs.existsSync(PY)) { console.error('模拟 venv 不存在:', PY); process.exit(2); }

  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'qa_b3_'));
  const env = Object.assign({}, process.env, {
    DB_PATH: path.join(tmp, 'insurance.db'),
    ATTACHMENTS_DIR: path.join(tmp, 'attachments'),
    PORT: String(PORT),
    PYTHONIOENCODING: 'utf-8',
  });
  const logFd = fs.openSync(path.join(tmp, 'uvicorn.log'), 'w');
  const proc = spawn(PY, ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(PORT)],
    { cwd: SIM, env, stdio: ['ignore', logFd, logFd] });

  let browser = null;
  try {
    if (!await waitHealth()) {
      console.error('uvicorn 未就绪');
      console.error(fs.readFileSync(path.join(tmp, 'uvicorn.log'), 'utf-8').slice(-3000));
      process.exit(1);
    }
    R.notes.push('uvicorn ready');

    // ---- 预置数据（经真实 HTTP，非浏览器）----
    const m1 = (await api('POST', '/api/members', { name: '钱一', relation: '本人', birthDate: '1985-03-03', gender: '男' })).data;
    const m2 = (await api('POST', '/api/members', { name: '孙二', relation: '配偶', birthDate: '1987-07-07', gender: '女' })).data;
    async function mk(company, name, over) {
      const payload = Object.assign({
        company, insuranceType: '重疾险', productName: name,
        policyholderId: m1.id, insuredId: m2.id, effectiveDate: '2023-01-01',
        coverageTermType: '终身', coverageTermValue: null,
        paymentTermType: '固定年限', paymentTermValue: 20, paymentFrequency: '年缴',
        premiumPerPeriod: 500000, status: '生效中',
      }, over || {});
      return (await api('POST', '/api/policies', payload)).data;
    }
    const p1 = await mk('众安', 'E2E产品A', { advisorName: '原顾问A', advisorPhone: '1001', channelName: '渠道A' });
    const p2 = await mk('众安', 'E2E产品B', { advisorName: '原顾问B', advisorPhone: '1002', channelName: '渠道B' });
    const p3 = await mk('平安', 'E2E产品C', { advisorName: '原顾问C' });
    R.notes.push('seeded 2 members / 3 policies');

    // ---- 启动浏览器 ----
    browser = await puppeteer.launch({
      headless: 'shell',
      args: ['--no-sandbox', '--disable-setuid-sandbox', '--no-proxy-server', '--disable-dev-shm-usage'],
    });
    const page = await browser.newPage();
    await page.setViewport({ width: 1440, height: 1000 });
    page.on('console', (msg) => {
      if (msg.type() === 'error') R.consoleErrors.push(msg.text());
    });
    page.on('pageerror', (err) => R.pageErrors.push(String(err && err.message ? err.message : err)));
    page.on('requestfailed', (req) => {
      R.requestFailures.push({ url: req.url(), err: req.failure() && req.failure().errorText });
    });

    const p = (sel) => page.waitForSelector(sel, { timeout: 10000 });

    // ============ 打开保单列表 ============
    await page.goto(`${BASE}/#/policies`, { waitUntil: 'networkidle2' });
    await p('#view-policies .data-table');
    assert('列表视图渲染出表格', true);
    const rowCount = await page.$$eval('#view-policies .data-table tbody tr', (r) => r.length);
    assert('表格行数 = 3', rowCount === 3, { rowCount });

    // ============ ④ 勾选状态跨重渲染保持 ============
    await page.click(`.row-select[data-id="${p1.id}"]`);
    await sleep(150);
    let btnText = await page.$eval('#btn-batch-edit', (b) => b.textContent.trim());
    assert('勾选 1 张后按钮计数为 (1)', btnText.includes('(1)'), { btnText });
    // 触发一次重渲染（筛选变化 → paint）
    await page.select('#f-type', '重疾险');
    await sleep(200);
    await page.select('#f-type', '');
    await sleep(200);
    const stillChecked = await page.$eval(`.row-select[data-id="${p1.id}"]`, (c) => c.checked);
    btnText = await page.$eval('#btn-batch-edit', (b) => b.textContent.trim());
    assert('重渲染后勾选态保持', stillChecked === true);
    assert('重渲染后按钮计数仍为 (1)', btnText.includes('(1)'), { btnText });

    // ============ ① 批量编辑三态：设值 + 留空不改 ============
    await page.click(`.row-select[data-id="${p2.id}"]`); // 选中 p1+p2
    await sleep(150);
    await page.click('#btn-batch-edit');
    await p('.batch-edit');
    await page.type('.batch-edit [name="advisorName"]', 'E2E统一顾问');
    await page.click('.modal-footer button.btn.primary');
    await sleep(800);
    const modalGone = await page.$('.batch-edit');
    assert('批量保存后弹窗关闭', modalGone === null);
    const q1 = (await api('GET', `/api/policies/${p1.id}`)).data;
    const q2 = (await api('GET', `/api/policies/${p2.id}`)).data;
    assert('批量设值：p1.advisorName 已改', q1.advisorName === 'E2E统一顾问', { v: q1.advisorName });
    assert('批量设值：p2.advisorName 已改', q2.advisorName === 'E2E统一顾问', { v: q2.advisorName });
    assert('批量留空：p1.advisorPhone 未被改', q1.advisorPhone === '1001', { v: q1.advisorPhone });
    assert('批量留空：p2.channelName 未被改', q2.channelName === '渠道B', { v: q2.channelName });

    // ============ ① 批量编辑三态：高级「设为空」 ============
    await page.click(`.row-select[data-id="${p1.id}"]`);
    await sleep(150);
    await page.click('#btn-batch-edit');
    await p('.batch-edit');
    await page.click('.batch-edit #be-advanced');
    await sleep(120);
    const clearVisible = await page.$eval('.batch-edit [data-clear="advisorPhone"]', (el) => el.closest('.clear-toggle').hidden === false);
    assert('高级模式显示「设为空」开关', clearVisible === true);
    await page.click('.batch-edit [data-clear="advisorPhone"]');
    await sleep(120);
    await page.click('.modal-footer button.btn.primary');
    await sleep(800);
    const q1b = (await api('GET', `/api/policies/${p1.id}`)).data;
    assert('显式清空：p1.advisorPhone 变为 null', q1b.advisorPhone === null, { v: q1b.advisorPhone });
    assert('清空未波及 p1.advisorName', q1b.advisorName === 'E2E统一顾问', { v: q1b.advisorName });

    // ============ ① 批量编辑校验：未填任何字段 → 阻止 ============
    await page.click(`.row-select[data-id="${p1.id}"]`);
    await sleep(150);
    await page.click('#btn-batch-edit');
    await p('.batch-edit');
    await page.click('.modal-footer button.btn.primary');
    await sleep(400);
    const errText = await page.$eval('.batch-edit .form-error', (el) => (el.hidden ? '' : el.textContent.trim()));
    assert('未填字段时阻止保存并提示', errText.includes('未填写任何'), { errText });
    await page.click('.modal-footer button.btn.ghost'); // 取消
    await sleep(300);

    // ============ ② 编辑弹窗 collect() 7 字段落库 ============
    await page.goto(`${BASE}/#/policies`, { waitUntil: 'networkidle2' });
    await p('#view-policies .data-table');
    await page.click(`[data-action="edit"][data-id="${p3.id}"]`);
    await p('.after-sales-grid');
    const fields = {
      advisorName: '王顾问', advisorPhone: '13800000000', advisorWechat: 'wx_wang',
      advisorEmail: 'wang@example.com', companyPhone: '1010-9955', channelName: '蚂蚁保', channelPhone: '95188',
    };
    for (const [k, v] of Object.entries(fields)) {
      const sel = `.after-sales-grid [name="${k}"]`;
      // 编辑弹窗会预填既有值，须先清空再输入（否则变成追加）
      await page.$eval(sel, (el) => { el.value = ''; });
      await page.type(sel, v);
    }
    await page.click('.modal-footer button.btn.primary');
    await sleep(900);
    const q3 = (await api('GET', `/api/policies/${p3.id}`)).data;
    const persisted = Object.fromEntries(Object.keys(fields).map((k) => [k, q3[k]]));
    const allPersist = Object.entries(fields).every(([k, v]) => q3[k] === v);
    assert('collect() 7 字段全部落库', allPersist, persisted);

    // ============ ③ 三入口常用值 datalist ============
    await page.goto(`${BASE}/#/policies`, { waitUntil: 'networkidle2' });
    await p('#view-policies .data-table');
    const dlOpts = (sel) => page.$$eval(sel, (els) => els.map((e) => e.getAttribute('value')));

    // 注：`.suggestion-datalists` 是 `.after-sales-grid` 的**兄弟节点**（同在 fieldset 内），
    //     故选择器不能写成 `.after-sales-grid .suggestion-datalists`。
    // 入口 A：编辑弹窗
    await page.click(`[data-action="edit"][data-id="${p3.id}"]`);
    await p('.suggestion-datalists');
    await sleep(400);
    let opts = await dlOpts('.suggestion-datalists datalist#pf-advisorName option');
    assert('入口A(编辑弹窗) 常用值含「王顾问」', opts.includes('王顾问'), { opts });
    await page.click('.modal-footer button.btn.ghost');
    await sleep(300);

    // 入口 B：列表页「+ 新增保单」
    await page.click('#btn-add-policy');
    await p('.suggestion-datalists');
    await sleep(500);
    opts = await dlOpts('.suggestion-datalists datalist#pf-advisorName option');
    assert('入口B(列表新增) 常用值含「王顾问」', opts.includes('王顾问'), { opts });
    await page.click('.modal-footer button.btn.ghost');
    await sleep(300);

    // 入口 C：顶栏「+ 新增保单」
    await page.click('#btn-new-policy');
    await p('.suggestion-datalists');
    await sleep(800);
    opts = await dlOpts('.suggestion-datalists datalist#pf-advisorName option');
    assert('入口C(顶栏新增) 常用值含「王顾问」', opts.includes('王顾问'), { opts });
    await page.click('.modal-footer button.btn.ghost');
    await sleep(300);

    // ============ ⑤ 附件全流程 ============
    const samplePdf = path.join(process.env.QA_BROWSER_DIR || os.tmpdir(), 'sample_e2e.pdf');
    fs.writeFileSync(samplePdf, Buffer.from('%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n', 'latin1'));
    await page.goto(`${BASE}/#/policies`, { waitUntil: 'networkidle2' });
    await p('#view-policies .data-table');
    await page.click(`[data-action="edit"][data-id="${p3.id}"]`);
    await p('.attachment-list');
    await sleep(500);
    const emptyText = await page.$eval('.attachment-list [data-role="items"]', (el) => el.textContent);
    assert('附件区初始为空', emptyText.includes('暂无附件'), { emptyText });
    const fileInput = await page.$('.attachment-list input[data-role="file"]');
    await fileInput.uploadFile(samplePdf);
    await page.waitForFunction(
      () => document.querySelectorAll('.attachment-list .attachment-item').length >= 1,
      { timeout: 8000 }
    );
    const countText = await page.$eval('.attachment-list [data-role="count"]', (el) => el.textContent);
    const attName = await page.$eval('.attachment-list .attachment-name', (el) => el.textContent);
    assert('上传后计数为 1', countText.includes('1'), { countText });
    assert('附件名展示 sample_e2e.pdf', attName.includes('sample_e2e.pdf'), { attName });

    // 查看：点击「查看」应调用 window.open 打开 inline content URL。
    // 说明：headless-shell 下「新标签」检测不稳定（noopener 会切断 opener 关系），
    //       改为拦截 window.open 断言其入参，更直接、更可靠，且不依赖浏览器行为细节。
    await page.evaluate(() => {
      window.__opened = [];
      window.open = (url, target, features) => { window.__opened.push({ url, target, features }); return null; };
    });
    await page.click('.attachment-list [data-act="view"]');
    await sleep(300);
    const opened = await page.evaluate(() => window.__opened);
    assert('「查看」调用 window.open 指向 inline content',
      opened.length >= 1 && String(opened[0].url).includes('/content?disposition=inline'), { opened });

    // 服务端 content 契约：inline / attachment
    const attId = (await api('GET', `/api/policies/${p3.id}/attachments`)).data.attachments[0].id;
    const resInline = await fetch(`${BASE}/api/policies/${p3.id}/attachments/${attId}/content?disposition=inline`);
    const cdInline = resInline.headers.get('content-disposition') || '';
    const ct = resInline.headers.get('content-type') || '';
    assert('content(inline) 200 且 Content-Type=application/pdf', resInline.status === 200 && ct.includes('pdf'), { ct });
    assert('content(inline) 头部为 inline', cdInline.startsWith('inline'), { cdInline });
    const resAtt = await fetch(`${BASE}/api/policies/${p3.id}/attachments/${attId}/content?disposition=attachment`);
    const cdAtt = resAtt.headers.get('content-disposition') || '';
    assert('content(attachment) 头部为 attachment', cdAtt.startsWith('attachment'), { cdAtt });

    // 下载：点击「下载」应创建并点击隐藏 <a> 指向 attachment content URL。
    // 说明：真实下载会触发浏览器下载管理器，导航被 abort（net::ERR_ABORTED）属**正常**，
    //       故拦截 anchor.click 断言其 href，避免把正常的下载行为误判为「请求失败」。
    await page.evaluate(() => {
      window.__downloads = [];
      HTMLAnchorElement.prototype.click = function () { if (this.href) window.__downloads.push(this.href); };
    });
    await page.click('.attachment-list [data-act="download"]');
    await sleep(400);
    const downloads = await page.evaluate(() => window.__downloads);
    assert('「下载」触发 <a> 指向 attachment content',
      downloads.some((u) => u.includes('/content?disposition=attachment')), { downloads });

    // 删除：内联二次确认
    await page.click('.attachment-list [data-act="delete"]');
    await sleep(200);
    const confirmShown = await page.$eval('.attachment-list [data-role="confirm"]', (el) => el.hidden === false);
    assert('删除出现内联二次确认', confirmShown === true);
    await page.click('.attachment-list [data-act="confirm-del"]');
    await page.waitForFunction(
      () => document.querySelectorAll('.attachment-list .attachment-item').length === 0,
      { timeout: 8000 }
    );
    const afterDel = await api('GET', `/api/policies/${p3.id}/attachments`);
    assert('删除后附件数为 0', afterDel.data.attachmentCount === 0, { c: afterDel.data.attachmentCount });

    // 关闭编辑弹窗
    await page.click('.modal-footer button.btn.ghost');
    await sleep(300);

    // ============ ⑦ 仪表盘 / 成员回归 ============
    await page.goto(`${BASE}/#/dashboard`, { waitUntil: 'networkidle2' });
    await sleep(800);
    const dashHtmlLen = await page.$eval('#view-dashboard', (el) => el.innerHTML.length);
    const dashVisible = await page.$eval('#view-dashboard', (el) => el.hidden === false);
    assert('仪表盘渲染且可见', dashVisible && dashHtmlLen > 200, { dashHtmlLen });
    await page.goto(`${BASE}/#/members`, { waitUntil: 'networkidle2' });
    await sleep(700);
    const memHtmlLen = await page.$eval('#view-members', (el) => el.innerHTML.length);
    const memVisible = await page.$eval('#view-members', (el) => el.hidden === false);
    assert('成员视图渲染且可见', memVisible && memHtmlLen > 100, { memHtmlLen });

    // ============ ⑥ 无 JS 异常 / console.error / 请求失败 ============
    assert('无 pageerror（未捕获异常）', R.pageErrors.length === 0, { errors: R.pageErrors });
    assert('无 console.error', R.consoleErrors.length === 0, { errors: R.consoleErrors });
    // ERR_ABORTED 是浏览器对「下载 / 主动导航中止」的正常表现（如附件下载），
    // 不属应用缺陷；此处单列并仅对「真实请求失败」判负。
    const realFailures = R.requestFailures.filter((f) => f.err !== 'net::ERR_ABORTED');
    assert('无真实请求失败', realFailures.length === 0,
      { realFailures, aborted: R.requestFailures.length - realFailures.length });

  } catch (err) {
    R.notes.push('EXCEPTION: ' + (err && err.stack ? err.stack : String(err)));
  } finally {
    if (browser) await browser.close().catch(() => {});
    proc.kill();
    try { fs.closeSync(logFd); } catch (e) { /* ignore */ }
  }

  const passed = R.assertions.filter((a) => a.pass).length;
  R.summary = { total: R.assertions.length, passed, failed: R.assertions.length - passed };
  console.log(JSON.stringify(R, null, 2));
  process.exit(R.summary.failed === 0 && !R.notes.some((n) => n.startsWith('EXCEPTION')) ? 0 : 1);
}

main();
