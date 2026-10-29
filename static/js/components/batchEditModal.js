/**
 * 批量编辑弹窗（v1.2 / T8）。
 *
 * `openBatchEdit(selectedPolicies, suggestions, onDone)`：
 * - 顶部标题「批量编辑（将修改 N 张保单）」+ 语义提示「只填写要修改的字段，留空 = 不改」；
 * - 7 个字段分三组（服务顾问 / 投保渠道 / 保险公司），其中 6 个挂 `<datalist>` 常用值；
 * - 「高级：我要把某些字段清空」勾选后，每个字段出现「设为空」——与「留空 = 不改」严格区分，
 *   对应服务端**三态语义**（键缺失 = 不改 / string = 设值 / `null` = 清空，见 ARCHITECTURE.md 6.9）；
 * - 若修改了「保险公司 · 联系电话」且所选保单**跨多家公司**，保存前内联二次确认（B-15）；
 * - 校验：未填写任何要修改的字段 → 阻止保存并提示（B-14）。
 *
 * 该弹窗**自行调用** `api.batchUpdatePolicies`，成功后回调 `onDone()`（由调用方刷新列表）。
 * 仅使用既有 `modal.js` 单例弹窗，不引入任何第三方依赖。
 */

import * as api from '../api.js';
import * as store from '../store.js';
import { escapeHtml } from '../format.js';
import { openModal } from './modal.js';
import { datalistId, suggestionDatalistsHtml } from './suggestions.js';

/** 7 个可批量修改字段的元数据（camelCase，与后端 `values` 键一致）。 */
const FIELD_META = {
  advisorName: { label: '服务顾问 · 姓名', maxlength: 20 },
  advisorPhone: { label: '服务顾问 · 电话', maxlength: 30 },
  advisorWechat: { label: '服务顾问 · 微信号', maxlength: 50 },
  advisorEmail: { label: '服务顾问 · 邮箱', maxlength: 80 },
  channelName: { label: '投保渠道 · 渠道名称', maxlength: 30 },
  channelPhone: { label: '投保渠道 · 渠道电话', maxlength: 30 },
  companyPhone: { label: '保险公司 · 联系电话', maxlength: 50 },
};

/** 分组渲染顺序（贴合 PRD 9.5.4 草图）。 */
const FIELD_GROUPS = [
  { legend: '服务顾问', fields: ['advisorName', 'advisorPhone', 'advisorWechat', 'advisorEmail'] },
  { legend: '投保渠道', fields: ['channelName', 'channelPhone'] },
  { legend: '保险公司', fields: ['companyPhone'] },
];

/** 挂常用值的 6 个字段（`companyPhone` 无历史聚合，见 ARCHITECTURE.md 6.9）。 */
const SUGGESTION_FIELDS = new Set([
  'advisorName',
  'advisorPhone',
  'advisorWechat',
  'advisorEmail',
  'channelName',
  'channelPhone',
]);

/**
 * 统计所选保单涉及的「保险公司」去重集合（用于跨公司二次确认判断）。
 * @param {Array} policies
 * @returns {Set<string>}
 */
function distinctCompanies(policies) {
  const set = new Set();
  (policies || []).forEach((p) => {
    const name = p && p.company != null ? String(p.company).trim() : '';
    if (name) set.add(name);
  });
  return set;
}

/**
 * 生成单个字段的 HTML（输入框 + 可选常用值 + 高级「设为空」开关）。
 * @param {string} name camelCase 字段名
 * @returns {string}
 */
function fieldHtml(name) {
  const meta = FIELD_META[name];
  const listAttr = SUGGESTION_FIELDS.has(name) ? ` list="${datalistId('be', name)}"` : '';
  return `<div class="field">
    <span class="field-label">${escapeHtml(meta.label)}</span>
    <input type="text" name="${name}" maxlength="${meta.maxlength}"${listAttr} placeholder="留空 = 不改" autocomplete="off">
    <label class="clear-toggle" hidden><input type="checkbox" data-clear="${name}"> 设为空</label>
  </div>`;
}

/**
 * 打开批量编辑弹窗。
 * @param {Array} selectedPolicies 已勾选的保单（来自列表视图）
 * @param {Record<string, Array<{value:string,count:number}>>} suggestions 售后字段常用值
 * @param {() => void} [onDone] 保存成功后的回调（用于刷新列表）
 */
export function openBatchEdit(selectedPolicies, suggestions, onDone) {
  const policies = Array.isArray(selectedPolicies) ? selectedPolicies : [];
  const count = policies.length;
  if (count === 0) {
    store.notify('请先勾选要修改的保单', 'error');
    return;
  }
  const ids = policies.map((p) => p.id);
  const multiCompany = distinctCompanies(policies).size > 1;

  const body = document.createElement('div');
  body.className = 'batch-edit';
  body.innerHTML = `
    <p class="batch-tip">只填写你要修改的字段；留空的字段保持各保单原值不变。</p>
    ${FIELD_GROUPS.map(
      (group) => `<fieldset class="fieldset">
        <legend class="fieldset-legend">${escapeHtml(group.legend)}</legend>
        <div class="contact-grid">${group.fields.map(fieldHtml).join('')}</div>
      </fieldset>`
    ).join('')}
    <label class="advanced-toggle">
      <input type="checkbox" id="be-advanced"> 高级：我要把某些字段清空
    </label>
    <p class="form-error" hidden></p>
    <div class="inline-confirm" hidden>
      <p class="inline-confirm-text"></p>
      <div class="inline-confirm-actions">
        <button type="button" class="btn ghost small" data-role="cancel-confirm">返回修改</button>
        <button type="button" class="btn primary small" data-role="do-confirm">确认修改</button>
      </div>
    </div>
    ${suggestionDatalistsHtml(suggestions, 'be')}
  `;

  const errorEl = body.querySelector('.form-error');
  const confirmEl = body.querySelector('.inline-confirm');
  const confirmTextEl = body.querySelector('.inline-confirm-text');
  const advancedCb = body.querySelector('#be-advanced');
  /** @param {string} name */
  const fieldInput = (name) => body.querySelector(`[name="${name}"]`);

  /** @type {{close: () => void}|null} */
  let handle = null;
  /** @type {Record<string, string|null>|null} */
  let pendingValues = null;

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

  /** 收起内联二次确认。 */
  function hideConfirm() {
    confirmEl.hidden = true;
    confirmTextEl.textContent = '';
    pendingValues = null;
  }

  /**
   * 切换「高级：设为空」模式。
   * @param {boolean} on
   */
  function setAdvanced(on) {
    body.querySelectorAll('.clear-toggle').forEach((el) => {
      el.hidden = !on;
    });
    if (!on) {
      // 关闭高级模式时，复位所有「设为空」勾选与输入框
      body.querySelectorAll('[data-clear]').forEach((cb) => {
        cb.checked = false;
        const input = fieldInput(cb.dataset.clear);
        if (input) {
          input.disabled = false;
          input.placeholder = '留空 = 不改';
        }
      });
    }
    hideConfirm();
  }

  /**
   * 收集三态 values：键缺失 = 不改；string = 设值；`null` = 清空。
   * @returns {{ok: boolean, values?: Record<string, string|null>, message?: string}}
   */
  function collect() {
    const values = {};
    Object.keys(FIELD_META).forEach((name) => {
      const clearCb = body.querySelector(`[data-clear="${name}"]`);
      if (clearCb && clearCb.checked) {
        values[name] = null; // 显式清空
        return;
      }
      const input = fieldInput(name);
      const value = input ? input.value.trim() : '';
      if (value) values[name] = value; // 设值
      // 否则：键缺失 = 不改
    });
    if (Object.keys(values).length === 0) {
      return { ok: false, message: '未填写任何要修改的字段' };
    }
    return { ok: true, values };
  }

  /**
   * 执行保存（调用批量编辑接口）。
   * @param {Record<string, string|null>} values
   * @returns {Promise<boolean>} 成功返回 true
   */
  async function doSave(values) {
    try {
      const result = await api.batchUpdatePolicies(ids, values);
      const updated = result && result.updated != null ? result.updated : count;
      store.notify(`已更新 ${updated} 张保单`, 'success');
      if (typeof onDone === 'function') onDone();
      return true;
    } catch (error) {
      showError(error.message);
      return false;
    }
  }

  /**
   * 展示内联二次确认（跨公司修改保险公司电话）。
   * @param {Record<string, string|null>} values
   */
  function requestConfirm(values) {
    pendingValues = values;
    const raw = values.companyPhone;
    const display = raw == null ? '（清空）' : `「${raw}」`;
    confirmTextEl.textContent = `将把 ${count} 张保单的保险公司电话统一改为 ${display}，可能不适用于所有公司。`;
    confirmEl.hidden = false;
  }

  // 高级模式开关
  advancedCb.addEventListener('change', () => setAdvanced(advancedCb.checked));

  // 各字段「设为空」勾选
  body.querySelectorAll('[data-clear]').forEach((cb) => {
    cb.addEventListener('change', () => {
      const input = fieldInput(cb.dataset.clear);
      if (!input) return;
      if (cb.checked) {
        input.value = '';
        input.disabled = true;
        input.placeholder = '将清空该字段';
      } else {
        input.disabled = false;
        input.placeholder = '留空 = 不改';
      }
      hideConfirm();
    });
  });

  // 内联确认操作
  confirmEl.querySelector('[data-role="cancel-confirm"]').addEventListener('click', hideConfirm);
  confirmEl.querySelector('[data-role="do-confirm"]').addEventListener('click', async (event) => {
    if (!pendingValues) return;
    const btn = event.currentTarget;
    btn.disabled = true;
    const ok = await doSave(pendingValues);
    if (ok) {
      if (handle) handle.close();
    } else {
      btn.disabled = false;
    }
  });

  handle = openModal({
    title: `批量编辑（将修改 ${count} 张保单）`,
    body,
    confirmText: '保存',
    cancelText: '取消',
    onConfirm: async () => {
      const result = collect();
      if (!result.ok) {
        showError(result.message);
        return false;
      }
      showError('');
      if ('companyPhone' in result.values && multiCompany) {
        requestConfirm(result.values);
        return false; // 保持弹窗打开，等待内联二次确认
      }
      return doSave(result.values);
    },
  });
}
