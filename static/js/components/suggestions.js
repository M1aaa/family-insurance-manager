/**
 * 「常用值」`<datalist>` 生成工具（v1.2）。
 *
 * 采用浏览器**原生 `<datalist>`**：零依赖、无自定义下拉组件、无新 bug 面。
 * 服务端已按「出现次数降序、同次数按值升序」返回，`<option value="王小明" label="出现在 8 张保单">`
 * 即满足 R-29 的「按次数降序、去重、选中即填入本字段、不跨字段带出」。
 *
 * - `label` 的可见性依赖浏览器（Chrome 不显示 label，Firefox 等部分浏览器显示），
 *   属**可接受降级**，不影响核心验收（选项内容与排序）。
 * - **`companyPhone` 不参与**（后端也不返回），故此处只处理 6 个字段。
 */

import { escapeHtml } from '../format.js';

/** 可挂 datalist 的 6 个售后字段（camelCase，与后端响应键一致）。 */
export const SUGGESTION_FIELDS = [
  'advisorName',
  'advisorPhone',
  'advisorWechat',
  'advisorEmail',
  'channelName',
  'channelPhone',
];

/**
 * 生成单个 `<datalist>`。
 * @param {string} id datalist 的 id（供 `<input list="...">` 引用）
 * @param {Array<{value: string, count: number}>} items 常用值（已按次数降序）
 * @returns {string} HTML 字符串
 */
export function datalistHtml(id, items) {
  const options = (items || [])
    .map((item) => {
      const value = item && item.value != null ? String(item.value) : '';
      if (!value) return '';
      const count = item && item.count != null ? Number(item.count) : null;
      const label = count != null && count > 0 ? ` label="出现在 ${count} 张保单"` : '';
      return `<option value="${escapeHtml(value)}"${label}></option>`;
    })
    .join('');
  return `<datalist id="${escapeHtml(id)}">${options}</datalist>`;
}

/**
 * 生成某个字段对应的 datalist id（表单与批量弹窗用不同 prefix，避免 id 冲突）。
 * @param {string} prefix
 * @param {string} field camelCase 字段名
 * @returns {string}
 */
export function datalistId(prefix, field) {
  return `${prefix}-${field}`;
}

/**
 * 一次性生成 6 个 `<datalist>`。
 * @param {Record<string, Array<{value:string,count:number}>>} suggestions 常用值聚合结果
 * @param {string} [prefix] id 前缀（默认 `sugg`）
 * @returns {string} HTML 字符串
 */
export function suggestionDatalistsHtml(suggestions, prefix = 'sugg') {
  const data = suggestions || {};
  return SUGGESTION_FIELDS.map((field) => datalistHtml(datalistId(prefix, field), data[field])).join('');
}
