/**
 * 保单表单组件。
 *
 * `createPolicyForm(enums, members, policy, suggestions?)` 返回 `{ element, collect, showError, updatePreview }`：
 * - 字段联动：保障期限「终身」→ 禁用并清空数值；缴费期限「趸交」→ 禁用并清空数值；
 * - 底部**实时预览**：前端复刻 ARCHITECTURE.md 5.3 的 1)/2) 步，展示预计总缴费期数与年化保费；
 * - `collect()` 做与后端 `PolicyCreate` 对齐的前端校验，返回 `{ ok, payload }` 或 `{ ok: false }`。
 *
 * 字段（camelCase）与后端一致：company / insuranceType / productName / policyholderId /
 * insuredIds / primaryInsuredId / effectiveDate / coverageTermType / coverageTermValue /
 * paymentTermType / paymentTermValue / paymentFrequency / premiumPerPeriod / insuredAmount /
 * paidPeriodsOverride / status / remark。
 *
 * v1.3：被保人由单选改为**复选框多选**（≥1）+ **主被保人 radio**（仅已选可选、必选）；
 * 取消勾选主被保人时自动转移给剩余已选成员的第一位；年龄预览以主被保人为准。
 *
 * `paidPeriodsOverride`（已缴期数）用于记录缴费进度：留空 = 后端按生效日期自动推算，
 * 填写 = 以用户记录为准（可纠正漏缴 / 提前缴 / 补录历史）。
 */

import { escapeHtml, fmtMoney, todayStr, enumDefault } from '../format.js';
import { suggestionDatalistsHtml, datalistId } from './suggestions.js';
import * as store from '../store.js';

// 新建保单时「缴费期限」的默认项：后端 PaymentTermType 枚举顺序为
// [趸交, 固定年限, 交至某年龄]，既有默认取「固定年限」（第 2 项）。
// 此处按**列表位置**派生默认值（而非硬编码枚举值），越界时自动回退首项。
const DEFAULT_PAYMENT_TERM_INDEX = 1;

/**
 * 解析 `YYYY-MM-DD` 为 {y, m, d}（避免时区干扰）。
 * @param {string} value
 * @returns {{y: number, m: number, d: number}}
 */
function parseDate(value) {
  const [y, m, d] = String(value).split('-').map(Number);
  return { y, m, d };
}

/**
 * 计算「已满周岁」（与后端 calc.age_years 口径一致）。
 * @param {string} birth `YYYY-MM-DD`
 * @param {string} on `YYYY-MM-DD`
 * @returns {number}
 */
function ageYears(birth, on) {
  if (!birth || !on) return 0;
  const b = parseDate(birth);
  const o = parseDate(on);
  let years = o.y - b.y;
  if (o.m < b.m || (o.m === b.m && o.d < b.d)) years -= 1;
  return years;
}

/**
 * 生成 `<option>` 列表。
 * @param {string[]} values
 * @param {string} selected
 * @returns {string}
 */
function optionsHtml(values, selected) {
  return (values || [])
    .map((v) => `<option value="${escapeHtml(v)}"${v === selected ? ' selected' : ''}>${escapeHtml(v)}</option>`)
    .join('');
}

/**
 * 生成成员 `<option>` 列表（值为成员 id）。
 * @param {Array} members
 * @param {string} selected
 * @returns {string}
 */
function memberOptionsHtml(members, selected) {
  return (members || [])
    .map((m) => `<option value="${escapeHtml(m.id)}"${m.id === selected ? ' selected' : ''}>${escapeHtml(m.name)}（${escapeHtml(m.relation)}）</option>`)
    .join('');
}

/**
 * 生成「被保人多选」区块：每名成员一个复选框 + 一个「主被保人」单选（v1.3）。
 *
 * - 复选框 `name="insuredIds"`，主被保人单选 `name="primaryInsuredId"`（仅已勾选可选）；
 * - 编辑回填：优先取 `data.insuredIds`，否则降级 `data.insuredId`（单值）；
 * - 主被保人默认 = `data.primaryInsuredId || data.insuredId`。
 * @param {Array} members
 * @param {object} data 现有保单（新增时为空对象）
 * @returns {string}
 */
function insuredOptionsHtml(members, data) {
  const list = members || [];
  if (list.length === 0) {
    return '<div class="muted">暂无家庭成员，请先到「家庭成员」页新增成员。</div>';
  }
  const selected = new Set(
    Array.isArray(data.insuredIds) && data.insuredIds.length
      ? data.insuredIds
      : data.insuredId
        ? [data.insuredId]
        : []
  );
  const primary = data.primaryInsuredId || data.insuredId || '';
  return list
    .map((m) => {
      const checked = selected.has(m.id);
      const isPrimary = checked && m.id === primary;
      return `<div class="insured-option">
        <label class="insured-check">
          <input type="checkbox" name="insuredIds" value="${escapeHtml(m.id)}"${checked ? ' checked' : ''}>
          <span>${escapeHtml(m.name)}（${escapeHtml(m.relation)}）</span>
        </label>
        <label class="primary-pick">
          <input type="radio" name="primaryInsuredId" value="${escapeHtml(m.id)}"${isPrimary ? ' checked' : ''}${checked ? '' : ' disabled'}>
          <span>主被保人</span>
        </label>
      </div>`;
    })
    .join('');
}

/**
 * 生成「已缴期数」字段的提示文案。
 *
 * 说明「留空」的含义；编辑既有保单时给出后端已算出的自动推算值作为参照，
 * 避免在前端重复实现 ``compute_k_max`` 的月末钳制逻辑（保持单一真源）。
 * @param {object} data 现有保单（新增时为空对象）
 * @returns {string}
 */
function paidHintHtml(data) {
  const parts = ['用于记录「已缴到第几期」，可判断当年是否已缴。留空则由生效日期自动推算。'];
  const auto = data && data.expectedPeriodsToDate != null ? Number(data.expectedPeriodsToDate) : null;
  const total = data && data.totalPeriods != null ? Number(data.totalPeriods) : null;
  if (auto !== null && total !== null) {
    parts.push(`当前按生效日期自动推算为第 ${auto} 期 / 共 ${total} 期。`);
  }
  return parts.map((text) => escapeHtml(text)).join(' ');
}

/**
 * 创建保单表单。
 * @param {object} enums 枚举字典（来自 /api/meta/enums）
 * @param {Array} members 成员列表（用于投保人 / 被保人下拉与年龄计算）
 * @param {object|null} policy 编辑时的现有保单；新增时传 null
 * @param {Record<string, Array<{value:string,count:number}>>} [suggestions] 售后字段常用值（来自 store.ensureSuggestions）；**留空时表单会自行异步拉取**（失败静默降级为空），因此顶栏入口无需额外传参也有常用值提示
 * @returns {{element: HTMLElement, collect: () => {ok: boolean, payload?: object}, showError: (msg: string) => void, updatePreview: () => void}}
 */
export function createPolicyForm(enums, members, policy, suggestions = {}) {
  const data = policy || {};
  const wrap = document.createElement('div');
  wrap.className = 'form-grid';
  wrap.innerHTML = `
    <label class="field">
      <span class="field-label">保险公司 <em>*</em></span>
      <input type="text" name="company" maxlength="50" value="${escapeHtml(data.company || '')}" placeholder="如：平安人寿">
    </label>
    <label class="field">
      <span class="field-label">险种 <em>*</em></span>
      <select name="insuranceType">${optionsHtml(enums.insuranceType, data.insuranceType || enumDefault(enums.insuranceType))}</select>
    </label>
    <label class="field span-2">
      <span class="field-label">产品名称 <em>*</em></span>
      <input type="text" name="productName" maxlength="80" value="${escapeHtml(data.productName || '')}" placeholder="如：平安福 2021">
    </label>
    <label class="field">
      <span class="field-label">投保人 <em>*</em></span>
      <select name="policyholderId">${memberOptionsHtml(members, data.policyholderId)}</select>
    </label>
    <div class="field span-2">
      <span class="field-label">被保人（可多选） <em>*</em></span>
      <div class="insured-options">${insuredOptionsHtml(members, data)}</div>
      <span class="field-hint">勾选至少 1 名被保人；并指定 1 名主被保人（保障期限「至某年龄」与年龄预览以主被保人为准）。</span>
    </div>
    <label class="field">
      <span class="field-label">生效日期 <em>*</em></span>
      <input type="date" name="effectiveDate" value="${escapeHtml(data.effectiveDate || todayStr())}">
    </label>
    <label class="field">
      <span class="field-label">保单状态</span>
      <select name="status">${optionsHtml(enums.policyStatus, data.status || enumDefault(enums.policyStatus))}</select>
    </label>
    <label class="field">
      <span class="field-label">保障期限 <em>*</em></span>
      <select name="coverageTermType">${optionsHtml(enums.coverageTermType, data.coverageTermType || enumDefault(enums.coverageTermType))}</select>
    </label>
    <label class="field">
      <span class="field-label">保障期限数值</span>
      <input type="number" name="coverageTermValue" min="1" max="120" value="${data.coverageTermValue != null ? data.coverageTermValue : ''}" placeholder="年限或年龄">
    </label>
    <label class="field">
      <span class="field-label">缴费期限 <em>*</em></span>
      <select name="paymentTermType">${optionsHtml(enums.paymentTermType, data.paymentTermType || enumDefault(enums.paymentTermType, DEFAULT_PAYMENT_TERM_INDEX))}</select>
    </label>
    <label class="field">
      <span class="field-label">缴费期限数值</span>
      <input type="number" name="paymentTermValue" min="1" max="120" value="${data.paymentTermValue != null ? data.paymentTermValue : ''}" placeholder="年限或年龄">
    </label>
    <label class="field">
      <span class="field-label">缴费方式 <em>*</em></span>
      <select name="paymentFrequency">${optionsHtml(enums.paymentFrequency, data.paymentFrequency || enumDefault(enums.paymentFrequency))}</select>
    </label>
    <label class="field">
      <span class="field-label">每期保费（元） <em>*</em></span>
      <input type="number" name="premiumPerPeriod" min="0" step="0.01" value="${data.premiumPerPeriod != null ? data.premiumPerPeriod : ''}" placeholder="如：500">
    </label>
    <label class="field">
      <span class="field-label">保额（元）</span>
      <input type="number" name="insuredAmount" min="0" step="0.01" value="${data.insuredAmount != null ? data.insuredAmount : ''}" placeholder="可选">
    </label>
    <label class="field span-2">
      <span class="field-label">已缴期数（缴费记录）</span>
      <input type="number" name="paidPeriodsOverride" min="0" max="1200" step="1" value="${data.paidPeriodsOverride != null ? data.paidPeriodsOverride : ''}" placeholder="留空 = 按生效日期自动推算">
      <span class="field-hint">${paidHintHtml(data)}</span>
    </label>
    <fieldset class="fieldset span-2">
      <legend class="fieldset-legend">售后信息（选填）</legend>
      <div class="after-sales-grid">
        <label class="field">
          <span class="field-label">服务顾问 · 姓名</span>
          <input type="text" name="advisorName" maxlength="20" list="${datalistId('pf', 'advisorName')}" value="${escapeHtml(data.advisorName || '')}" placeholder="如：王小明">
        </label>
        <label class="field">
          <span class="field-label">服务顾问 · 电话</span>
          <input type="text" name="advisorPhone" maxlength="30" list="${datalistId('pf', 'advisorPhone')}" value="${escapeHtml(data.advisorPhone || '')}" placeholder="如：13800000000">
        </label>
        <label class="field">
          <span class="field-label">服务顾问 · 微信号</span>
          <input type="text" name="advisorWechat" maxlength="50" list="${datalistId('pf', 'advisorWechat')}" value="${escapeHtml(data.advisorWechat || '')}" placeholder="如：wx_wangxm">
        </label>
        <label class="field">
          <span class="field-label">服务顾问 · 邮箱</span>
          <input type="text" name="advisorEmail" maxlength="80" list="${datalistId('pf', 'advisorEmail')}" value="${escapeHtml(data.advisorEmail || '')}" placeholder="如：wxm@example.com">
        </label>
        <label class="field">
          <span class="field-label">保险公司 · 公司名</span>
          <input type="text" class="readonly-field" value="${escapeHtml(data.company || '')}" placeholder="（沿用上方「保险公司」）" readonly tabindex="-1" aria-readonly="true">
        </label>
        <label class="field">
          <span class="field-label">保险公司 · 联系电话</span>
          <input type="text" name="companyPhone" maxlength="50" value="${escapeHtml(data.companyPhone || '')}" placeholder="如：1010-9955/952299">
        </label>
        <label class="field">
          <span class="field-label">投保渠道 · 渠道名称</span>
          <input type="text" name="channelName" maxlength="30" list="${datalistId('pf', 'channelName')}" value="${escapeHtml(data.channelName || '')}" placeholder="如：支付宝蚂蚁保">
        </label>
        <label class="field">
          <span class="field-label">投保渠道 · 渠道电话</span>
          <input type="text" name="channelPhone" maxlength="30" list="${datalistId('pf', 'channelPhone')}" value="${escapeHtml(data.channelPhone || '')}" placeholder="如：95188">
        </label>
      </div>
      <p class="hint-inline">售后信息全部选填，留空不影响保单保存与统计。</p>
      <div class="suggestion-datalists">${suggestionDatalistsHtml(suggestions, 'pf')}</div>
    </fieldset>
    <label class="field">
      <span class="field-label">备注</span>
      <input type="text" name="remark" maxlength="200" value="${escapeHtml(data.remark || '')}" placeholder="可选">
    </label>
    <div class="preview span-2"></div>
    <p class="form-error span-2" hidden></p>
  `;

  const errorEl = wrap.querySelector('.form-error');
  const previewEl = wrap.querySelector('.preview');
  const field = (name) => wrap.querySelector(`[name="${name}"]`);
  const datalistWrap = wrap.querySelector('.suggestion-datalists');

  /**
   * 写入 / 刷新售后字段「常用值」`<datalist>`。
   *
   * 三个入口（顶栏新增 / 列表页新增 / 编辑弹窗）共用本表单：调用方可显式传入 `suggestions`；
   * **未传入时（如顶栏入口）表单自行异步拉取并回填**——`ensureSuggestions` 内部已 try/catch，
   * 失败静默降级为空对象，**绝不阻塞表单渲染**。
   * @param {Record<string, Array<{value:string,count:number}>>} data
   */
  function applySuggestions(data) {
    if (!datalistWrap) return;
    datalistWrap.innerHTML = suggestionDatalistsHtml(data || {}, 'pf');
  }
  applySuggestions(suggestions);
  if (!suggestions || Object.keys(suggestions).length === 0) {
    // 调用方未提供常用值：表单自行获取（ensureSuggestions 内部已 try/catch，失败返回 {}）
    store.ensureSuggestions()
      .then((data) => {
        if (data && Object.keys(data).length) applySuggestions(data);
      })
      .catch(() => {
        // 常用值仅为「锦上添花」，任何异常都不得影响表单使用
      });
  }

  /**
   * 展示 / 清除表单级错误。
   * @param {string} message
   */
  function showError(message) {
    if (!message) {
      errorEl.hidden = true;
      errorEl.textContent = '';
      return;
    }
    errorEl.hidden = false;
    errorEl.textContent = message;
  }

  /**
   * 读取当前勾选的被保人 id（DOM 顺序）。
   * @returns {string[]}
   */
  function selectedInsuredIds() {
    return Array.from(wrap.querySelectorAll('input[name="insuredIds"]:checked')).map(
      (cb) => cb.value
    );
  }

  /**
   * 读取当前主被保人 id（未选时为 `''`）。
   * @returns {string}
   */
  function primaryInsuredId() {
    const radio = wrap.querySelector('input[name="primaryInsuredId"]:checked');
    return radio ? radio.value : '';
  }

  /**
   * 同步被保人区块：未勾选行的主被保人单选禁用并取消勾选；
   * 若主被保人不在已选集合内，则**自动转移**给剩余已选成员的第一位。
   */
  function syncInsuredUI() {
    const selected = new Set(selectedInsuredIds());
    wrap.querySelectorAll('.insured-option').forEach((opt) => {
      const checkbox = opt.querySelector('input[name="insuredIds"]');
      const radio = opt.querySelector('input[name="primaryInsuredId"]');
      const on = checkbox.checked;
      radio.disabled = !on;
      if (!on && radio.checked) radio.checked = false;
    });
    const current = primaryInsuredId();
    if (!current || !selected.has(current)) {
      const first = selectedInsuredIds()[0] || '';
      wrap.querySelectorAll('input[name="primaryInsuredId"]').forEach((radio) => {
        radio.checked = radio.value === first;
      });
    }
  }

  /** 字段联动：按保障 / 缴费期限类型启用或禁用对应数值输入。 */
  function syncLinkage() {
    const coverageType = field('coverageTermType').value;
    const coverageValue = field('coverageTermValue');
    if (coverageType === '终身') {
      coverageValue.value = '';
      coverageValue.disabled = true;
      coverageValue.placeholder = '终身无需填写';
    } else {
      coverageValue.disabled = false;
      coverageValue.placeholder = coverageType === '固定年限' ? '年限 1–100' : '年龄 1–120';
    }

    const paymentType = field('paymentTermType').value;
    const paymentValue = field('paymentTermValue');
    if (paymentType === '趸交') {
      paymentValue.value = '';
      paymentValue.disabled = true;
      paymentValue.placeholder = '趸交无需填写';
    } else {
      paymentValue.disabled = false;
      paymentValue.placeholder = paymentType === '固定年限' ? '年限 1–100' : '年龄 1–120';
    }
  }

  /**
   * 计算表单当前的「总缴费期数 / 年化保费 / 保障期限文案」。
   *
   * 复刻 ARCHITECTURE.md 5.3 的 1)/2) 步；被 `updatePreview` 与 `collect` 共用。
   * @returns {{annualized: number, totalPeriods: number, coverageText: string}}
   */
  function computeTotals() {
    const freq = field('paymentFrequency').value;
    const perYear = freq === '月缴' ? 12 : 1;
    const premium = Math.max(0, Number(field('premiumPerPeriod').value) || 0);
    const paymentType = field('paymentTermType').value;
    const paymentValue = Number(field('paymentTermValue').value) || 0;
    const coverageType = field('coverageTermType').value;
    const coverageValue = Number(field('coverageTermValue').value) || 0;
    const effectiveDate = field('effectiveDate').value;
    const insured = (members || []).find((m) => m.id === primaryInsuredId());

    let annualized;
    let totalPeriods;
    if (paymentType === '趸交') {
      annualized = 0;
      totalPeriods = 1;
    } else if (paymentType === '固定年限') {
      annualized = freq === '年缴' ? premium : premium * 12;
      totalPeriods = paymentValue * perYear;
    } else {
      // 交至某年龄：总期数 = max(0, 目标年龄 - 生效时年龄) × 每年期数
      annualized = freq === '年缴' ? premium : premium * 12;
      const ageAtEffective = ageYears(insured && insured.birthDate, effectiveDate);
      totalPeriods = Math.max(0, paymentValue - ageAtEffective) * perYear;
    }

    let coverageText;
    if (coverageType === '终身') coverageText = '终身';
    else if (coverageType === '固定年限') coverageText = coverageValue ? `保 ${coverageValue} 年` : '—';
    else coverageText = coverageValue ? `保至 ${coverageValue} 岁` : '—';

    return { annualized, totalPeriods, coverageText };
  }

  /** 实时预览总缴费期数与年化保费。 */
  function updatePreview() {
    const { annualized, totalPeriods, coverageText } = computeTotals();

    previewEl.innerHTML = `
      <div class="preview-item"><span>预计总缴费期数</span><strong>${totalPeriods > 0 ? `${totalPeriods} 期` : '—'}</strong></div>
      <div class="preview-item"><span>年化保费</span><strong>${fmtMoney(annualized)}</strong></div>
      <div class="preview-item"><span>保障期限</span><strong>${escapeHtml(coverageText)}</strong></div>
    `;
  }

  /**
   * 读取数值输入框（空 → null）。
   * @param {HTMLInputElement} input
   * @param {boolean} asInt
   * @returns {number|null}
   */
  function readNumber(input, asInt) {
    const raw = input.value.trim();
    if (raw === '') return null;
    const n = Number(raw);
    if (!Number.isFinite(n)) return null;
    return asInt ? Math.round(n) : n;
  }

  /**
   * 收集并校验表单数据。
   * @returns {{ok: boolean, payload?: object}}
   */
  function collect() {
    const fail = (message) => {
      showError(message);
      return { ok: false };
    };

    const company = field('company').value.trim();
    const insuranceType = field('insuranceType').value;
    const productName = field('productName').value.trim();
    const policyholderId = field('policyholderId').value;
    const insuredIds = selectedInsuredIds();
    const primaryId = primaryInsuredId();
    const effectiveDate = field('effectiveDate').value;
    const coverageTermType = field('coverageTermType').value;
    const coverageTermValue = readNumber(field('coverageTermValue'), true);
    const paymentTermType = field('paymentTermType').value;
    const paymentTermValue = readNumber(field('paymentTermValue'), true);
    const paymentFrequency = field('paymentFrequency').value;
    const premiumPerPeriod = readNumber(field('premiumPerPeriod'), false);
    const insuredAmount = readNumber(field('insuredAmount'), false);
    const paidPeriodsOverride = readNumber(field('paidPeriodsOverride'), true);
    const status = field('status').value;
    const remark = field('remark').value.trim();
    // ---- v1.2 售后信息（表单语义：全部提交，清空某框 = 清空该字段）----
    const advisorName = field('advisorName').value.trim();
    const advisorPhone = field('advisorPhone').value.trim();
    const advisorWechat = field('advisorWechat').value.trim();
    const advisorEmail = field('advisorEmail').value.trim();
    const companyPhone = field('companyPhone').value.trim();
    const channelName = field('channelName').value.trim();
    const channelPhone = field('channelPhone').value.trim();

    if (!company) return fail('请填写保险公司');
    if (company.length > 50) return fail('保险公司名称不能超过 50 个字符');
    if (!productName) return fail('请填写产品名称');
    if (productName.length > 80) return fail('产品名称不能超过 80 个字符');
    if (!policyholderId) return fail('请选择投保人');
    if (insuredIds.length === 0) return fail('请至少选择 1 名被保人');
    if (!primaryId || !insuredIds.includes(primaryId)) {
      return fail('请指定主被保人（须为已选被保人之一）');
    }
    if (!effectiveDate) return fail('请选择生效日期');

    if (coverageTermType === '终身') {
      if (coverageTermValue !== null) return fail('保障期限为「终身」时无需填写数值');
    } else if (coverageTermType === '固定年限') {
      if (coverageTermValue === null) return fail('请填写保障期限年限');
      if (coverageTermValue < 1 || coverageTermValue > 100) return fail('保障期限年限须在 1–100 之间');
    } else {
      if (coverageTermValue === null) return fail('请填写保障至的年龄');
      if (coverageTermValue < 1 || coverageTermValue > 120) return fail('保障年龄须在 1–120 之间');
    }

    if (paymentTermType === '趸交') {
      if (paymentTermValue !== null) return fail('缴费期限为「趸交」时无需填写数值');
    } else if (paymentTermType === '固定年限') {
      if (paymentTermValue === null) return fail('请填写缴费期限年限');
      if (paymentTermValue < 1 || paymentTermValue > 100) return fail('缴费期限年限须在 1–100 之间');
    } else {
      if (paymentTermValue === null) return fail('请填写缴费至的年龄');
      if (paymentTermValue < 1 || paymentTermValue > 120) return fail('缴费年龄须在 1–120 之间');
    }

    if (premiumPerPeriod === null || !Number.isFinite(premiumPerPeriod)) return fail('请填写每期保费');
    if (premiumPerPeriod <= 0) return fail('每期保费必须大于 0');
    if (insuredAmount !== null && insuredAmount < 0) return fail('保额不能为负');

    // 已缴期数（缴费记录）：非负、≤ 1200，并在总期数可确定时做交叉校验（与后端 PolicyBase 对齐）。
    if (paidPeriodsOverride !== null) {
      if (paidPeriodsOverride < 0) return fail('已缴期数不能为负');
      if (paidPeriodsOverride > 1200) return fail('已缴期数过大（上限 1200 期）');
      if (paymentTermType === '趸交') {
        if (paidPeriodsOverride > 1) return fail('趸交保单已缴期数不能超过 1 期');
      } else if (paymentTermType === '固定年限') {
        const maxPeriods = paymentTermValue * (paymentFrequency === '月缴' ? 12 : 1);
        if (maxPeriods > 0 && paidPeriodsOverride > maxPeriods) {
          return fail(`已缴期数不能超过总缴费期数（${maxPeriods} 期）`);
        }
      }
      // 「交至某年龄」的总期数取决于被保人出生日期，后端 calc_schedule 会做防御性钳制，
      // 前端不预判上限，避免误报。
    }

    if (remark.length > 200) return fail('备注不能超过 200 个字符');

    showError('');
    return {
      ok: true,
      payload: {
        company,
        insuranceType,
        productName,
        policyholderId,
        insuredIds,
        primaryInsuredId: primaryId,
        effectiveDate,
        coverageTermType,
        coverageTermValue,
        paymentTermType,
        paymentTermValue,
        paymentFrequency,
        premiumPerPeriod,
        insuredAmount,
        paidPeriodsOverride,
        advisorName: advisorName || null,
        advisorPhone: advisorPhone || null,
        advisorWechat: advisorWechat || null,
        advisorEmail: advisorEmail || null,
        companyPhone: companyPhone || null,
        channelName: channelName || null,
        channelPhone: channelPhone || null,
        status,
        remark: remark || null,
      },
    };
  }

  /** 售后区块中「保险公司 · 公司名」只读展示：实时镜像上方「保险公司」输入（不重复提交）。 */
  function syncCompanyDisplay() {
    const display = wrap.querySelector('.readonly-field');
    if (display) display.value = field('company').value;
  }

  // 任意输入变化时同步联动与预览
  wrap.addEventListener('input', () => {
    syncInsuredUI();
    syncLinkage();
    syncCompanyDisplay();
    updatePreview();
  });
  wrap.addEventListener('change', () => {
    syncInsuredUI();
    syncLinkage();
    syncCompanyDisplay();
    updatePreview();
  });
  syncInsuredUI();
  syncLinkage();
  syncCompanyDisplay();
  updatePreview();

  return { element: wrap, collect, showError, updatePreview };
}
