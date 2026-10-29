/**
 * 家庭成员视图（T10）。
 *
 * - 渲染成员表格（含保单数 / 生效中保单数 / 年化保费等派生字段）；
 * - 新增 / 编辑走通用弹窗 + `memberForm`；
 * - 删除走二次确认；若成员被保单引用，后端返回 409 `MEMBER_IN_USE`，
 *   此处直接展示后端 `message`（如「该成员关联 N 份保单，无法删除」）。
 *
 * v1.7：「计入统计」列改为**只读状态列**（标题「统计状态」），
 * 修改入口统一到首页「统计范围筛选」；编辑成员时回填现值 → **不再改动** `countInStats`。
 */

import * as api from '../api.js';
import * as store from '../store.js';
import { fmtMoney, fmtInt, fmtDate, escapeHtml } from '../format.js';
import { openModal, confirmModal } from '../components/modal.js';
import { createMemberForm } from '../components/memberForm.js';

/**
 * 「已有保障类型」单元格（v1.3）。
 *
 * 每行渲染为若干 `.chip`（`险种 × 张数`，后端已按「张数降序 → 险种枚举顺序」排好）；
 * 点击任一标签**展开明细**（张数 / 生效中张数 / 分摊年化）；无保障显示「暂无保障」。
 * @param {object} m 成员（含 coverageTypes）
 * @returns {string}
 */
function coverageCell(m) {
  const coverage = Array.isArray(m.coverageTypes) ? m.coverageTypes : [];
  if (coverage.length === 0) return '<span class="chip-empty">暂无保障</span>';
  const chips = coverage
    .map(
      (c) => `<button type="button" class="chip" data-action="coverage" data-id="${escapeHtml(m.id)}">${escapeHtml(c.insuranceType)}<span class="chip-count">×${fmtInt(c.count)}</span></button>`
    )
    .join('');
  const lines = coverage
    .map(
      (c) => `<div class="coverage-line"><span>${escapeHtml(c.insuranceType)}</span><span>张数 ${fmtInt(c.count)} · 生效中 ${fmtInt(c.activeCount)} · 年化 ${fmtMoney(c.annualPremium)}</span></div>`
    )
    .join('');
  return `<div class="chip-row">${chips}</div><div class="coverage-detail" hidden>${lines}</div>`;
}

/**
 * 渲染成员表格。
 * @param {Array} members
 * @returns {string}
 */
function tableHtml(members) {
  const rows = members
    .map(
      (m) => `<tr>
        <td>${escapeHtml(m.name)}</td>
        <td>${escapeHtml(m.relation)}</td>
        <td>${fmtDate(m.birthDate)}</td>
        <td>${escapeHtml(m.gender)}</td>
        <td class="num">${fmtInt(m.policyCount)}</td>
        <td class="num">${fmtInt(m.activePolicyCount)}</td>
        <td class="num">${fmtMoney(m.annualPremium)}</td>
        <td>${coverageCell(m)}</td>
        <td>${escapeHtml(m.remark || '—')}</td>
        <td>${
          m.countInStats === false
            ? '<span class="badge off">不计入</span>'
            : '<span class="badge active">计入</span>'
        }</td>
        <td>
          <div class="row-actions">
            <button class="btn small" type="button" data-action="edit" data-id="${escapeHtml(m.id)}">编辑</button>
            <button class="btn small danger" type="button" data-action="delete" data-id="${escapeHtml(m.id)}">删除</button>
          </div>
        </td>
      </tr>`
    )
    .join('');

  return `<div class="panel table-panel">
    <div class="table-scroll">
      <table class="data-table">
        <thead>
          <tr>
            <th>姓名</th><th>关系</th><th>出生日期</th><th>性别</th>
            <th class="num">保单数</th><th class="num">生效中</th><th class="num">年化保费</th>
            <th>已有保障类型</th><th>备注</th><th title="在首页「统计范围筛选」中修改">统计状态</th><th>操作</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  </div>`;
}

/**
 * 渲染视图。
 * @param {HTMLElement} container
 */
export async function render(container) {
  container.innerHTML = '<div class="loading">加载中…</div>';
  let enums;
  try {
    enums = await store.ensureEnums();
    await store.refreshAll();
  } catch (error) {
    container.innerHTML = `<div class="empty-state">加载失败：${escapeHtml(error.message)}</div>`;
    return;
  }

  const members = store.get('members') || [];

  container.innerHTML = `
    <div class="view-header">
      <div>
        <h2 class="view-title">家庭成员</h2>
        <p class="view-sub">共 ${members.length} 位成员</p>
      </div>
      <button class="btn primary" type="button" id="btn-add-member">+ 新增成员</button>
    </div>
    ${
      members.length
        ? tableHtml(members)
        : '<div class="empty-state">还没有家庭成员，点击右上角「新增成员」开始整理。</div>'
    }
  `;

  container.querySelector('#btn-add-member').addEventListener('click', () => openCreate(enums));

  container.querySelectorAll('[data-action]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const { action, id } = btn.dataset;
      if (action === 'coverage') {
        // 点击保障类型标签 → 展开 / 收起该成员的明细
        const detail = btn.closest('td').querySelector('.coverage-detail');
        if (detail) detail.hidden = !detail.hidden;
        return;
      }
      const member = members.find((m) => m.id === id);
      if (!member) return;
      if (action === 'edit') openEdit(enums, member);
      else if (action === 'delete') removeMember(member);
    });
  });
}

/**
 * 打开新增成员弹窗。
 * @param {object} enums
 */
function openCreate(enums) {
  const form = createMemberForm(enums, null);
  openModal({
    title: '新增成员',
    body: form.element,
    confirmText: '保存',
    onConfirm: async () => {
      const result = form.collect();
      if (!result.ok) return false;
      try {
        await api.createMember(result.payload);
        store.notify('成员已新增', 'success');
        store.requestRefresh();
        return true;
      } catch (error) {
        form.showError(error.message);
        return false;
      }
    },
  });
}

/**
 * 打开编辑成员弹窗。
 * @param {object} enums
 * @param {object} member
 */
function openEdit(enums, member) {
  const form = createMemberForm(enums, member);
  openModal({
    title: `编辑成员 · ${member.name}`,
    body: form.element,
    confirmText: '保存',
    onConfirm: async () => {
      const result = form.collect();
      if (!result.ok) return false;
      try {
        // v1.7：表单已无「计入家庭保费统计」开关 → 回填现值，**保持 countInStats 不变**
        // （修改入口在首页「统计范围筛选」）。
        await api.updateMember(member.id, {
          ...result.payload,
          countInStats: member.countInStats !== false,
        });
        store.notify('成员已更新', 'success');
        store.requestRefresh();
        return true;
      } catch (error) {
        form.showError(error.message);
        return false;
      }
    },
  });
}

/**
 * 删除成员（二次确认）。
 * @param {object} member
 */
function removeMember(member) {
  confirmModal(
    `确定要删除成员「${member.name}」吗？删除后不可恢复。`,
    async () => {
      try {
        await api.deleteMember(member.id);
        store.notify('成员已删除', 'success');
        store.requestRefresh();
      } catch (error) {
        // 409 MEMBER_IN_USE 等：直接展示后端文案
        store.notify(error.message, 'error');
      }
      return true;
    },
    { confirmText: '删除' }
  );
}
