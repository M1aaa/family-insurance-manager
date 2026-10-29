/**
 * 极简前端状态容器。
 *
 * 职责：
 * 1. 缓存 `enums / members / policies / dashboard`，避免重复请求（如 `enums` 只取一次）；
 * 2. 提供 `ensureEnums()` / `refreshAll()` 统一拉取入口，保证三视图数据同源一致；
 * 3. 通过 `notify()` / `requestRefresh()` 派发**应用级自定义事件**，由 `main.js` 统一
 *    渲染轻提示与重渲染当前视图——视图与入口之间零直接耦合，无循环依赖。
 */

import * as api from './api.js';

/**
 * @type {{
 *   enums: any,
 *   members: any[]|null,
 *   policies: any[]|null,
 *   dashboard: any|null,
 *   exclusions: {excludedPolicyholderIds: string[], excludedInsuranceTypes: string[]}|null,
 *   fieldSuggestions: any|null
 * }}
 */
const state = {
  enums: null,
  members: null,
  policies: null,
  dashboard: null,
  exclusions: null,
  fieldSuggestions: null,
};

/**
 * 读取缓存。
 * @param {'enums'|'members'|'policies'|'dashboard'} key
 */
export function get(key) {
  return state[key];
}

/**
 * 写入缓存。
 * @param {'enums'|'members'|'policies'|'dashboard'} key
 * @param {any} value
 */
export function set(key, value) {
  state[key] = value;
}

/**
 * 确保枚举字典已加载（仅首次真正请求，之后走缓存）。
 * @returns {Promise<any>}
 */
export async function ensureEnums() {
  if (!state.enums) {
    state.enums = await api.getEnums();
  }
  return state.enums;
}

/**
 * 确保「售后字段常用值」已加载（仅首次真正请求，之后走缓存）。
 *
 * **失败降级**：请求失败时返回空对象 `{}` 并缓存，避免表单 / 批量弹窗打不开。
 * @returns {Promise<Record<string, Array<{value:string, count:number}>>>}
 */
export async function ensureSuggestions() {
  if (!state.fieldSuggestions) {
    try {
      state.fieldSuggestions = await api.getFieldSuggestions();
    } catch (error) {
      // 降级为空对象：常用值只是「锦上添花」，不应阻塞表单
      state.fieldSuggestions = {};
    }
  }
  return state.fieldSuggestions;
}

/**
 * 一次性刷新成员 / 保单 / 看板，保证三视图数据一致。
 * @returns {Promise<typeof state>}
 */
export async function refreshAll() {
  const [members, policies, dashboard, exclusions] = await Promise.all([
    api.getMembers(),
    api.getPolicies(),
    api.getDashboard(),
    api.getExclusions(),
  ]);
  state.members = members;
  state.policies = policies;
  state.dashboard = dashboard;
  state.exclusions = exclusions;
  // 数据已变化，常用值缓存作废（下次 ensureSuggestions 会重新拉取）
  state.fieldSuggestions = null;
  return state;
}

/**
 * 派发轻提示事件（由 `main.js` 渲染到 toast 元素）。
 * @param {string} message
 * @param {'info'|'success'|'error'} [type]
 */
export function notify(message, type = 'info') {
  window.dispatchEvent(new CustomEvent('app:toast', { detail: { message, type } }));
}

/**
 * 派发数据刷新事件（由 `main.js` 重渲染当前视图）。
 */
export function requestRefresh() {
  window.dispatchEvent(new Event('app:refresh'));
}

export { state };
