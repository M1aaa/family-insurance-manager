/**
 * 家庭成员表单组件。
 *
 * `createMemberForm(enums, member)` 返回 `{ element, collect, showError }`：
 * - `element`  可挂载到弹窗的表单 DOM；
 * - `collect()` 做前端校验，返回 `{ ok, payload }` 或 `{ ok: false }`；
 * - `showError(msg)` 展示 / 清除表单级错误（供后端 409 文案回填）。
 *
 * 字段与后端 `MemberCreate` 对齐（camelCase）：name / relation / birthDate / gender / remark。
 *
 * v1.7：**移除**「计入家庭保费统计」开关——该维度改由首页「统计范围筛选」统一管理
 * （见 `views/dashboard.js`）；编辑成员**不再**改动 `countInStats`（由 `views/members.js`
 * 在提交时回填现值以保持兼容）。
 */

import { escapeHtml, todayStr, enumDefault } from '../format.js';

// 新建成员时「性别」的默认项：后端 Gender 枚举顺序为 [男, 女, 未知]，
// 既有默认取「未知」（第 3 项）。按**列表位置**派生（而非硬编码枚举值），越界回退首项。
const DEFAULT_GENDER_INDEX = 2;

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
 * 创建成员表单。
 * @param {{relation: string[], gender: string[]}} enums 枚举字典（来自 /api/meta/enums）
 * @param {object|null} member 编辑时的现有成员；新增时传 null
 * @returns {{element: HTMLElement, collect: () => {ok: boolean, payload?: object}, showError: (msg: string) => void}}
 */
export function createMemberForm(enums, member) {
  const data = member || {};
  const wrap = document.createElement('div');
  wrap.className = 'form-grid';
  wrap.innerHTML = `
    <label class="field">
      <span class="field-label">姓名 <em>*</em></span>
      <input type="text" name="name" maxlength="20" value="${escapeHtml(data.name || '')}" placeholder="如：张三">
    </label>
    <label class="field">
      <span class="field-label">与本人关系 <em>*</em></span>
      <select name="relation">${optionsHtml(enums.relation, data.relation || enumDefault(enums.relation))}</select>
    </label>
    <label class="field">
      <span class="field-label">出生日期 <em>*</em></span>
      <input type="date" name="birthDate" value="${escapeHtml(data.birthDate || '')}">
    </label>
    <label class="field">
      <span class="field-label">性别</span>
      <select name="gender">${optionsHtml(enums.gender, data.gender || enumDefault(enums.gender, DEFAULT_GENDER_INDEX))}</select>
    </label>
    <label class="field span-2">
      <span class="field-label">备注</span>
      <input type="text" name="remark" maxlength="100" value="${escapeHtml(data.remark || '')}" placeholder="可选">
    </label>
    <p class="form-error span-2" hidden></p>
  `;

  const errorEl = wrap.querySelector('.form-error');
  const field = (name) => wrap.querySelector(`[name="${name}"]`);

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
   * 收集并校验表单数据。
   * @returns {{ok: boolean, payload?: object}}
   */
  function collect() {
    const name = field('name').value.trim();
    const relation = field('relation').value;
    const birthDate = field('birthDate').value;
    const gender = field('gender').value;
    const remark = field('remark').value.trim();

    if (!name) {
      showError('请填写姓名');
      return { ok: false };
    }
    if (name.length > 20) {
      showError('姓名不能超过 20 个字符');
      return { ok: false };
    }
    if (!birthDate) {
      showError('请选择出生日期');
      return { ok: false };
    }
    if (birthDate > todayStr()) {
      showError('出生日期不能晚于今天');
      return { ok: false };
    }
    if (remark.length > 100) {
      showError('备注不能超过 100 个字符');
      return { ok: false };
    }

    showError('');
    return {
      ok: true,
      payload: {
        name,
        relation,
        birthDate,
        gender,
        remark: remark || null,
      },
    };
  }

  return { element: wrap, collect, showError };
}
