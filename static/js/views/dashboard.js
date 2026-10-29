/**
 * 总览看板视图（T12，v1.7 扩展）。
 *
 * 数据全部来自单一接口 `GET /api/stats/dashboard`（见 ARCHITECTURE.md 4.5）。
 * 页面结构：
 * - **6 张**指标卡（① 当年应缴总保费 / ② 年化保费合计 / ③ 当年未缴费金额 /
 *   ④ 近 60 天待缴 / ⑤ 保单总数 / ⑥ 在保人数）：
 *   ① = 生效中 + 已缴清保单年化合计；② = 生效中且未缴清保单年化合计（v1.4 双口径，差额即已缴部分）；
 *   ③ = **本日历年到期且未缴的期次金额合计**（v1.7 现金流口径，不受筛选影响）；
 * - **统计范围筛选面板**（v1.7）：按投保人 / 按险种**临时**排除「年化保费」口径，
 *   不落库、不发请求、刷新即重置；「存为默认」经 `PUT /api/stats/exclusions` 持久化。
 *   仅当「临时 ≠ 默认」时前端才从 `/api/policies` 重算（默认态直接用后端数字 → 逐字节不变）；
 * - 成员保费分布：饼图 + 明细表，支持「按被保人 / 按投保人」切换；
 * - 险种保费分布：柱状图 + 明细表（用 enums 补零展示全部险种，见 A1）；
 * - 近 60 天待缴清单：按到期日升序，底部合计（与「待缴」卡片同源）；
 *   每张保单**最早**的一行提供「已缴」按钮 → `POST /api/policies/{id}/pay-next`；
 * - 逾期未缴清单（仅在有逾期时展示）：存在「已过缴费日但未标记已缴」的期次时，
 *   顶部同时给出警示横幅。
 *
 * ⚠️ 过滤范围铁律（唯一真源）：**仅「年化保费」口径**受排除筛选影响
 * （卡片 ① ② + 三维分布 + `/api/members` 年化）；
 * 计数 / `upcoming60*` / `overdue[]` / `paidToDatePremium` / `yearUnpaidPremium` **一律不过滤**。
 */

import * as api from '../api.js';
import * as store from '../store.js';
import { fmtMoney, fmtInt, fmtDate, escapeHtml, fmtInsuredNames, insuredTitle } from '../format.js';
import { renderPie, renderBar } from '../chart.js';

/** 警示图标（内联 SVG，避免任何外部资源依赖）。 */
const WARN_ICON = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="flex:0 0 auto;margin-top:2px"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`;

/**
 * 指标卡 HTML。
 * @param {string} label
 * @param {string} value 已格式化的展示值
 * @param {string} sub
 * @param {string} [extraClass] 追加类名（如 `accent` 高亮描边）
 * @returns {string}
 */
function metricCard(label, value, sub, extraClass) {
  return `<div class="metric-card${extraClass ? ` ${extraClass}` : ''}">
    <div class="metric-label">${escapeHtml(label)}</div>
    <div class="metric-value">${value}</div>
    <div class="metric-sub">${escapeHtml(sub)}</div>
  </div>`;
}

/**
 * 成员分布明细表。
 * @param {Array} rows
 * @returns {string}
 */
function memberTableHtml(rows) {
  if (!rows.length) return '<div class="chart-empty">暂无成员数据</div>';
  const body = rows
    .map(
      (r) => `<tr>
        <td>${escapeHtml(r.name)}</td>
        <td>${escapeHtml(r.relation)}</td>
        <td class="num">${fmtInt(r.policyCount)}</td>
        <td class="num">${fmtMoney(r.annualPremium)}</td>
        <td class="num">${fmtMoney(r.upcoming60Premium)}</td>
        <td class="num">${fmtMoney(r.paidToDatePremium)}</td>
      </tr>`
    )
    .join('');
  return `<div class="table-scroll">
    <table class="data-table compact">
      <thead>
        <tr>
          <th>成员</th><th>关系</th>
          <th class="num">保单数</th><th class="num">年化保费</th>
          <th class="num">近 60 天待缴</th><th class="num">累计已缴</th>
        </tr>
      </thead>
      <tbody>${body}</tbody>
    </table>
  </div>`;
}

/**
 * 险种分布明细表。
 * @param {Array} rows
 * @returns {string}
 */
function typeTableHtml(rows) {
  if (!rows.length) return '<div class="chart-empty">暂无险种数据</div>';
  const body = rows
    .map(
      (r) => `<tr>
        <td>${escapeHtml(r.insuranceType)}</td>
        <td class="num">${fmtInt(r.policyCount)}</td>
        <td class="num">${fmtMoney(r.annualPremium)}</td>
        <td class="num">${fmtMoney(r.upcoming60Premium)}</td>
        <td class="num">${fmtMoney(r.paidToDatePremium)}</td>
      </tr>`
    )
    .join('');
  return `<div class="table-scroll">
    <table class="data-table compact">
      <thead>
        <tr>
          <th>险种</th>
          <th class="num">保单数</th><th class="num">年化保费</th>
          <th class="num">近 60 天待缴</th><th class="num">累计已缴</th>
        </tr>
      </thead>
      <tbody>${body}</tbody>
    </table>
  </div>`;
}

/**
 * 「已缴」按钮 HTML。
 * @param {object} item 待缴 / 逾期清单行
 * @param {string} label 按钮文案
 * @returns {string}
 */
function payButtonHtml(item, label) {
  return `<button type="button" class="btn small" data-pay-policy="${escapeHtml(item.policyId)}" data-pay-name="${escapeHtml(item.productName)}">${escapeHtml(label)}</button>`;
}

/**
 * 近 60 天待缴清单表。
 *
 * 「已缴」按钮只出现在每张保单**最早**的一行：`pay-next` 登记的是「最早未缴期次」，
 * 只有最早那一行的期次是明确无歧义的；同一保单的后续行显示占位符。
 * 若该保单已存在逾期期次，则不给按钮，改为提示先补缴——避免按钮语义错位。
 * @param {Array} items 待缴清单
 * @param {Set<string>} overduePolicyIds 存在逾期期次的保单 id 集合
 * @returns {string}
 */
function upcomingTableHtml(items, overduePolicyIds) {
  if (!items.length) return '<div class="empty-state">未来 60 天内没有待缴期次。</div>';
  const seen = new Set();
  const body = items
    .map((i) => {
      const firstOfPolicy = !seen.has(i.policyId);
      seen.add(i.policyId);
      let action;
      if (!firstOfPolicy) {
        action = '<span class="progress-text">—</span>';
      } else if (overduePolicyIds.has(i.policyId)) {
        action = '<span class="badge warn">有逾期，请先补缴</span>';
      } else {
        action = payButtonHtml(i, '已缴');
      }
      return `<tr>
        <td>${fmtDate(i.dueDate)}</td>
        <td>${escapeHtml(i.company)}</td>
        <td>${escapeHtml(i.productName)}</td>
        <td title="${escapeHtml(insuredTitle(i.insuredNames))}">${escapeHtml(fmtInsuredNames(i.insuredNames))}</td>
        <td>${escapeHtml(`第 ${i.periodIndex}/${i.totalPeriods} 期`)}</td>
        <td class="num">${fmtMoney(i.premiumPerPeriod)}</td>
        <td>${action}</td>
      </tr>`;
    })
    .join('');
  return `<div class="table-scroll">
    <table class="data-table">
      <thead>
        <tr>
          <th>到期日</th><th>保险公司</th><th>产品</th><th>被保人</th>
          <th>期次</th><th class="num">金额</th><th>操作</th>
        </tr>
      </thead>
      <tbody>${body}</tbody>
    </table>
  </div>`;
}

/**
 * 逾期未缴清单表。
 * @param {Array} items
 * @returns {string}
 */
function overdueTableHtml(items) {
  if (!items.length) return '<div class="empty-state">没有逾期未缴的期次。</div>';
  const body = items
    .map(
      (i) => `<tr>
        <td>${fmtDate(i.earliestDueDate)}</td>
        <td>${escapeHtml(i.company)}</td>
        <td>${escapeHtml(i.productName)}</td>
        <td title="${escapeHtml(insuredTitle(i.insuredNames))}">${escapeHtml(fmtInsuredNames(i.insuredNames))}</td>
        <td class="num">${fmtInt(i.overduePeriods)} 期</td>
        <td class="num">${fmtMoney(i.amount)}</td>
        <td>${payButtonHtml(i, '补缴一期')}</td>
      </tr>`
    )
    .join('');
  return `<div class="table-scroll">
    <table class="data-table">
      <thead>
        <tr>
          <th>最早应缴日</th><th>保险公司</th><th>产品</th><th>被保人</th>
          <th class="num">逾期期数</th><th class="num">逾期金额</th><th>操作</th>
        </tr>
      </thead>
      <tbody>${body}</tbody>
    </table>
  </div>`;
}

/**
 * 两个集合是否相等（用于判断「临时筛选 == 已保存默认」）。
 * @param {Set<string>} a
 * @param {Set<string>} b
 * @returns {boolean}
 */
function setEquals(a, b) {
  if (a.size !== b.size) return false;
  for (const value of a) {
    if (!b.has(value)) return false;
  }
  return true;
}

/**
 * 把 ``totalCents``（分）均摊到 ``n`` 份：镜像后端 ``calc.allocate_amounts``。
 *
 * - ``base = round_half_up(total / n)``；前 ``n-1`` 份 = ``base``，**尾差归最后一份**；
 * - 全程整数「分」运算，保证 ``Σ shares == total``。
 * @param {number} totalCents
 * @param {number} n
 * @returns {number[]}
 */
function allocateAmountsCents(totalCents, n) {
  if (n <= 0) return [];
  if (n === 1) return [totalCents];
  const base = Math.floor(totalCents / n + 0.5); // ROUND_HALF_UP（金额恒为正）
  const shares = new Array(n).fill(base);
  shares[n - 1] = totalCents - base * (n - 1); // 尾差归最后一份
  return shares;
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

  const dash = store.get('dashboard');
  if (!dash) {
    container.innerHTML = '<div class="empty-state">暂无看板数据。</div>';
    return;
  }

  const overview = dash.overview || {};
  const byMember = dash.byMember || { byInsured: [], byPolicyholder: [] };
  const byType = dash.byType || [];
  const upcoming = dash.upcoming || [];
  const overdue = dash.overdue || [];
  const members = store.get('members') || [];
  const policies = store.get('policies') || [];

  // ---- v1.7 统计范围筛选：已保存默认 + 临时（不落库，刷新即重置） ----
  const savedExclusions = store.get('exclusions') || {
    excludedPolicyholderIds: [],
    excludedInsuranceTypes: [],
  };
  const defaultHolders = new Set(savedExclusions.excludedPolicyholderIds || []);
  const defaultTypes = new Set(savedExclusions.excludedInsuranceTypes || []);
  let tempHolders = new Set(defaultHolders);
  let tempTypes = new Set(defaultTypes);
  /** 仅当「临时 ≠ 默认」时非 null，为前端重算结果（单位：元）。 */
  let currentOverride = null;
  let currentMode = 'byInsured';

  const overdueTotal = overdue.reduce((sum, i) => sum + (Number(i.amount) || 0), 0);
  const overduePolicyIds = new Set(overdue.map((i) => i.policyId));

  container.innerHTML = `
    <div class="view-header">
      <div>
        <h2 class="view-title">总览看板</h2>
        <p class="view-sub">数据截止 ${fmtDate(dash.asOfDate)}</p>
      </div>
    </div>
    ${
      overdue.length
        ? `<div class="alert warn">
            <span>${WARN_ICON}</span>
            <span>检测到 <strong>${fmtInt(overview.overduePolicyCount)}</strong> 份保单共 <strong>${fmtInt(
              overdue.reduce((sum, i) => sum + (Number(i.overduePeriods) || 0), 0)
            )}</strong> 期已过缴费日但未标记已缴，涉及金额 <strong>${fmtMoney(
              overdueTotal
            )}</strong>。请核对后补缴，或在保单编辑页修正「已缴期数」。</span>
          </div>`
        : ''
    }
    <div class="metrics" id="metrics-wrap">${metricsInner()}</div>
    ${filterPanelHtml()}
    <div class="panel-grid">
      <section class="panel">
        <div class="panel-head">
          <h3>成员保费分布</h3>
          <div class="segmented" id="member-mode">
            <button type="button" data-mode="byInsured" class="active">按被保人</button>
            <button type="button" data-mode="byPolicyholder">按投保人</button>
          </div>
        </div>
        <div id="member-chart"></div>
        <div id="member-table"></div>
      </section>
      <section class="panel">
        <div class="panel-head">
          <h3>险种保费分布</h3>
        </div>
        <div id="type-chart"></div>
        <div id="type-table"></div>
      </section>
    </div>
    <section class="panel">
      <div class="panel-head">
        <h3>近 60 天待缴清单</h3>
        <span class="panel-total">合计 <strong id="upcoming-total">${fmtMoney(0)}</strong></span>
      </div>
      <div id="upcoming-table"></div>
    </section>
    ${
      overdue.length
        ? `<section class="panel">
            <div class="panel-head">
              <h3>逾期未缴</h3>
              <span class="panel-total">合计 <strong id="overdue-total">${fmtMoney(overdueTotal)}</strong></span>
            </div>
            <div id="overdue-table"></div>
          </section>`
        : ''
    }
  `;

  const el = (selector) => container.querySelector(selector);

  /**
   * 成员关系排序权重（与后端 RELATION_ORDER 同源：取自枚举字典顺序）。
   * @param {string} relation
   * @returns {number}
   */
  function relationRank(relation) {
    const order = enums.relation || [];
    const idx = order.indexOf(relation);
    return idx === -1 ? order.length : idx;
  }

  /**
   * 用全部家庭成员对分组结果补零：无任何保单的成员也展示（金额 ¥0.00）。
   * 排序与后端一致：先按关系权重，再按姓名（码点序）。
   * @param {Array} baseRows 后端返回的成员分组行
   * @returns {Array}
   */
  function zeroFillMembers(baseRows) {
    const seen = new Set(baseRows.map((r) => r.memberId));
    const merged = baseRows.slice();
    for (const m of members) {
      if (!seen.has(m.id)) {
        merged.push({
          memberId: m.id,
          name: m.name,
          relation: m.relation,
          policyCount: 0,
          activePolicyCount: 0,
          annualPremium: 0,
          upcoming60Premium: 0,
          paidToDatePremium: 0,
        });
      }
    }
    merged.sort(
      (a, b) =>
        relationRank(a.relation) - relationRank(b.relation) ||
        (a.name < b.name ? -1 : a.name > b.name ? 1 : 0)
    );
    return merged;
  }

  /**
   * 被保人**规范序**（镜像后端 ``insured_service.insured_sort_key``：
   * 关系权重 → 姓名 → id）。决定分摊顺序与尾差归属。
   * @param {string[]} insuredIds
   * @param {Map<string, object>} byId
   * @returns {string[]}
   */
  function canonicalInsuredOrder(insuredIds, byId) {
    const arr = (insuredIds || []).slice();
    arr.sort((a, b) => {
      const ma = byId.get(a) || {};
      const mb = byId.get(b) || {};
      const ra = relationRank(ma.relation);
      const rb = relationRank(mb.relation);
      if (ra !== rb) return ra - rb;
      const na = ma.name || '';
      const nb = mb.name || '';
      if (na !== nb) return na < nb ? -1 : 1;
      return a < b ? -1 : a > b ? 1 : 0;
    });
    return arr;
  }

  /**
   * 从 ``/api/policies`` 重算「年化保费」口径（镜像后端），对**并集**（投保人 ∪ 险种）
   * 一次性过滤，从根本上避免两维重复扣减。
   * @returns {{annualTotal:number, unpaidAnnual:number,
   *   byType:Map<string,number>, byHolder:Map<string,number>, byInsured:Map<string,number>}}
   */
  function recomputeAnnualized() {
    const byId = new Map(members.map((m) => [m.id, m]));
    let annualTotalCents = 0;
    let unpaidCents = 0;
    const byTypeMap = new Map();
    const byHolderMap = new Map();
    const byInsuredMap = new Map();
    for (const p of policies) {
      if (tempHolders.has(p.policyholderId) || tempTypes.has(p.insuranceType)) continue;
      if (p.status !== '生效中' && p.status !== '已缴清') continue;
      const annualCents = Math.round((Number(p.annualizedPremium) || 0) * 100);
      annualTotalCents += annualCents;
      byTypeMap.set(p.insuranceType, (byTypeMap.get(p.insuranceType) || 0) + annualCents);
      byHolderMap.set(p.policyholderId, (byHolderMap.get(p.policyholderId) || 0) + annualCents);
      const ordered = canonicalInsuredOrder(p.insuredIds, byId);
      const shares = allocateAmountsCents(annualCents, ordered.length);
      ordered.forEach((mid, i) =>
        byInsuredMap.set(mid, (byInsuredMap.get(mid) || 0) + shares[i])
      );
      if (p.status === '生效中' && !p.isPaidOff) unpaidCents += annualCents;
    }
    return {
      annualTotal: annualTotalCents / 100,
      unpaidAnnual: unpaidCents / 100,
      byType: byTypeMap,
      byHolder: byHolderMap,
      byInsured: byInsuredMap,
    };
  }

  /** 临时筛选是否等于已保存默认。 */
  function isDefault() {
    return setEquals(tempHolders, defaultHolders) && setEquals(tempTypes, defaultTypes);
  }

  /** 卡片 ①② 副标题里的「不含 …」提示。 */
  function effectiveNote() {
    const parts = [];
    if (tempHolders.size) parts.push(`${tempHolders.size} 位投保人`);
    if (tempTypes.size) parts.push(`${tempTypes.size} 类险种`);
    return parts.length ? `（不含 ${parts.join('、')}）` : '';
  }

  /** 6 张指标卡 HTML（③ 始终取后端 ``yearUnpaidPremium``，不受筛选影响）。 */
  function metricsInner() {
    const ov = currentOverride
      ? {
          ...overview,
          annualTotalPremium: currentOverride.annualTotal,
          unpaidAnnualPremium: currentOverride.unpaidAnnual,
        }
      : overview;
    const note = effectiveNote();
    return `
      ${metricCard('当年应缴总保费', fmtMoney(ov.annualTotalPremium), `生效中 + 已缴清保单的年化保费合计${note}`)}
      ${metricCard('年化保费合计', fmtMoney(ov.unpaidAnnualPremium), `生效中且未缴清保单的年化保费合计${note}`, 'accent')}
      ${metricCard('当年未缴费金额', fmtMoney(overview.yearUnpaidPremium ?? 0), '本日历年到期且未缴的期次金额合计（不受筛选影响）', 'accent')}
      ${metricCard('近 60 天待缴', fmtMoney(overview.upcoming60Premium), `共 ${fmtInt(overview.upcoming60Count)} 笔待缴期次`)}
      ${metricCard('保单总数', fmtInt(overview.policyTotal), `生效中 ${fmtInt(overview.activePolicyCount)} 份`)}
      ${metricCard('在保人数', fmtInt(overview.insuredPersonCount), '保障期内的被保人（去重）')}`;
  }

  /** 统计范围筛选面板 HTML（勾选 = 计入；取消勾选 = 临时排除）。 */
  function filterPanelHtml() {
    const holderChips = members
      .slice()
      .sort(
        (a, b) =>
          relationRank(a.relation) - relationRank(b.relation) ||
          (a.name < b.name ? -1 : a.name > b.name ? 1 : 0)
      )
      .map(
        (m) =>
          `<label class="filter-chip"><input type="checkbox" data-kind="holder" data-id="${escapeHtml(
            m.id
          )}"${tempHolders.has(m.id) ? '' : ' checked'}><span>${escapeHtml(m.name)}</span></label>`
      )
      .join('');
    const typeChips = (enums.insuranceType || [])
      .map(
        (t) =>
          `<label class="filter-chip"><input type="checkbox" data-kind="type" data-id="${escapeHtml(
            t
          )}"${tempTypes.has(t) ? '' : ' checked'}><span>${escapeHtml(t)}</span></label>`
      )
      .join('');
    return `<section class="panel filter-panel">
      <div class="panel-head">
        <h3>统计范围筛选</h3>
        <div class="filter-actions">
          <button type="button" class="btn small" id="btn-reset-filter">重置为默认</button>
          <button type="button" class="btn small primary" id="btn-save-filter">存为默认</button>
        </div>
      </div>
      <p class="filter-hint">取消勾选即从「年化保费」口径中<b>临时</b>排除（不影响保单数、待缴清单与「当年未缴费金额」）；临时改动刷新即重置。点「存为默认」写入服务端长期生效。</p>
      <div class="filter-groups">
        <div class="filter-group">
          <div class="filter-group-title">按投保人</div>
          <div class="filter-chips" id="filter-holders">${holderChips || '<span class="filter-empty">暂无成员</span>'}</div>
        </div>
        <div class="filter-group">
          <div class="filter-group-title">按险种</div>
          <div class="filter-chips" id="filter-types">${typeChips || '<span class="filter-empty">暂无险种</span>'}</div>
        </div>
      </div>
    </section>`;
  }

  /** 把 checkbox 状态同步为当前临时集合。 */
  function syncFilterInputs() {
    container.querySelectorAll('#filter-holders input').forEach((cb) => {
      cb.checked = !tempHolders.has(cb.dataset.id);
    });
    container.querySelectorAll('#filter-types input').forEach((cb) => {
      cb.checked = !tempTypes.has(cb.dataset.id);
    });
  }

  /** 重绘 6 卡（① ② 用重算值，其余取后端）。 */
  function paintMetrics() {
    el('#metrics-wrap').innerHTML = metricsInner();
  }

  /**
   * 绘制成员分布（饼图 + 明细表）。
   * @param {'byInsured'|'byPolicyholder'} mode
   */
  function paintMember(mode) {
    currentMode = mode;
    const baseRows =
      (mode === 'byPolicyholder' ? byMember.byPolicyholder : byMember.byInsured) || [];
    let rows = zeroFillMembers(baseRows);
    if (currentOverride) {
      const map =
        mode === 'byPolicyholder' ? currentOverride.byHolder : currentOverride.byInsured;
      rows = rows.map((r) => ({ ...r, annualPremium: (map.get(r.memberId) || 0) / 100 }));
    }
    const chartData = rows
      .filter((r) => Number(r.annualPremium) > 0)
      .map((r) => ({ label: r.name, value: Number(r.annualPremium), display: fmtMoney(r.annualPremium) }));
    renderPie(el('#member-chart'), chartData, { emptyText: '暂无年化保费数据' });
    el('#member-table').innerHTML = memberTableHtml(rows);
  }

  /** 绘制险种分布（柱状图 + 明细表，含 0 值补零）。 */
  function paintType() {
    const map = new Map(byType.map((t) => [t.insuranceType, t]));
    let rows = (enums.insuranceType || []).map(
      (type) =>
        map.get(type) || {
          insuranceType: type,
          policyCount: 0,
          annualPremium: 0,
          upcoming60Premium: 0,
          paidToDatePremium: 0,
        }
    );
    if (currentOverride) {
      rows = rows.map((r) => ({
        ...r,
        annualPremium: (currentOverride.byType.get(r.insuranceType) || 0) / 100,
      }));
    }
    rows.sort((a, b) => Number(b.annualPremium) - Number(a.annualPremium));

    // ★ v1.6：柱状图（保留"谁大谁小"的直观对比）。
    // 为了不被误读成"进度条/只缴了一半"，做了三件事（详见 chart.js 的 renderBar）：
    //   ① **直角条**（圆角胶囊是进度条/按钮的视觉语言）
    //   ② **底部刻度轴 + 网格线**（显式说明这是测量刻度，空白是"刻度区内剩余"而非"未完成"）
    //   ③ 无灰色底轨（那会被读成"未缴部分"）
    // 待缴信息：条内同色加深段 + 图例下方小字。
    const chartData = rows
      .filter((r) => Number(r.annualPremium) > 0)
      .map((r) => {
        const annual = Number(r.annualPremium);
        const pending = Number(r.upcoming60Premium) || 0;
        return {
          label: r.insuranceType,
          value: annual,
          display: fmtMoney(annual),
          ratio: annual > 0 ? Math.min(1, pending / annual) : 0,
          sub: pending > 0 ? `待缴 ${fmtMoney(pending)}` : null,
        };
      });
    renderBar(el('#type-chart'), chartData, { emptyText: '暂无年化保费数据' });
    el('#type-table').innerHTML = typeTableHtml(rows);
  }

  /** 绘制近 60 天待缴清单与合计。 */
  function paintUpcoming() {
    const total = upcoming.reduce((sum, i) => sum + (Number(i.premiumPerPeriod) || 0), 0);
    el('#upcoming-total').textContent = fmtMoney(total);
    el('#upcoming-table').innerHTML = upcomingTableHtml(upcoming, overduePolicyIds);
    el('#upcoming-table').addEventListener('click', onPayClick);
  }

  /** 绘制逾期未缴清单与合计（无逾期时不渲染该区块）。 */
  function paintOverdue() {
    if (!overdue.length) return;
    el('#overdue-total').textContent = fmtMoney(overdueTotal);
    el('#overdue-table').innerHTML = overdueTableHtml(overdue);
    el('#overdue-table').addEventListener('click', onPayClick);
  }

  /** 应用临时筛选：仅当「临时 ≠ 默认」时重算，否则直接用后端数字（逐字节不变）。 */
  function applyFilter() {
    currentOverride = isDefault() ? null : recomputeAnnualized();
    paintMetrics();
    paintMember(currentMode);
    paintType();
  }

  /**
   * 处理「已缴 / 补缴一期」点击：登记一次缴费后刷新当前视图。
   *
   * 事件委托挂在 `#upcoming-table` / `#overdue-table` 上——这两个元素由
   * `innerHTML` 每次重建，因此监听器不会在重复渲染时累积。
   * @param {MouseEvent} event
   */
  async function onPayClick(event) {
    const btn = event.target.closest('button[data-pay-policy]');
    if (!btn || btn.disabled) return;
    const policyId = btn.dataset.payPolicy;
    const productName = btn.dataset.payName || '该保单';
    const original = btn.textContent;
    btn.disabled = true;
    btn.textContent = '处理中…';
    try {
      const updated = await api.payNextPeriod(policyId);
      store.notify(`已登记「${productName}」第 ${updated.paidPeriods} 期缴费`, 'success');
      store.requestRefresh();
    } catch (error) {
      btn.disabled = false;
      btn.textContent = original;
      store.notify(error.message, 'error');
    }
  }

  el('#member-mode').addEventListener('click', (event) => {
    const btn = event.target.closest('button[data-mode]');
    if (!btn) return;
    el('#member-mode')
      .querySelectorAll('button')
      .forEach((b) => b.classList.toggle('active', b === btn));
    paintMember(btn.dataset.mode);
  });

  // ---- v1.7 筛选面板交互（不落库、不发请求，仅重算展示） ----
  el('.filter-panel').addEventListener('change', (event) => {
    const cb = event.target.closest('input[type="checkbox"]');
    if (!cb) return;
    const { kind, id } = cb.dataset;
    const set = kind === 'holder' ? tempHolders : tempTypes;
    if (cb.checked) set.delete(id);
    else set.add(id);
    applyFilter();
  });

  el('#btn-reset-filter').addEventListener('click', () => {
    tempHolders = new Set(defaultHolders);
    tempTypes = new Set(defaultTypes);
    syncFilterInputs();
    applyFilter();
  });

  el('#btn-save-filter').addEventListener('click', async () => {
    const btn = el('#btn-save-filter');
    const original = btn.textContent;
    btn.disabled = true;
    btn.textContent = '保存中…';
    try {
      await api.saveExclusions({
        excludedPolicyholderIds: Array.from(tempHolders),
        excludedInsuranceTypes: Array.from(tempTypes),
      });
      store.notify('统计范围已保存为默认', 'success');
      store.requestRefresh();
    } catch (error) {
      btn.disabled = false;
      btn.textContent = original;
      store.notify(error.message, 'error');
    }
  });

  paintMember('byInsured');
  paintType();
  paintUpcoming();
  paintOverdue();
}
