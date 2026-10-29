/**
 * 原生 SVG 图表绘制模块（零依赖，纯字符串拼 SVG / HTML）。
 *
 * 提供三种图表：
 * - `renderPie(container, data, options)`         饼图 + 图例（含百分比）；
 * - `renderStackedBar(container, data, options)`  横向 100% 堆叠条 + 图例（占比构成，**看板险种分布用**）；
 * - `renderBar(container, data, options)`         横向柱状图（当前未被看板使用；保留为通用组件）。
 *
 * ⚠️ 选型注意（踩过的坑）：**不要用柱状图表达"占比构成"**。
 * 柱状图用「长度」编码数值，短的那根后面必然留白，读者会把它读成
 * 「没填满 / 只缴了一半」——这是长度编码本身的歧义，改配色、去底轨都救不回来。
 * 表达占比请用 `renderPie` 或 `renderStackedBar`（两者都是"整块/整条"语义）。
 *
 * 数据格式：`[{ label, value, color?, display?, sub? }]`，
 * `display` 为可选的自定义展示文案（如已格式化的金额），缺省时直接显示 `value`；
 * `sub` 为可选补充说明（如「待缴 ¥200.00」），显示在图例标签下方。
 * 空数据不报错，渲染占位提示。
 */

import { escapeHtml } from './format.js';

const DEFAULT_COLORS = [
  '#2f6feb', '#12b886', '#f59f00', '#e8590c', '#ae3ec9',
  '#1098ad', '#e03131', '#5c7cfa', '#37b24d', '#f76707',
];

/**
 * 横向 100% 堆叠条：表达「占比构成」。
 *
 * 为什么不用柱状图：用「**长度**」编码数值时，短的那根后面必然留白，
 * 会被误读成「没填满 / 只缴了一半」——这是长度编码本身的语义歧义，配色与底轨都改不掉。
 *
 * 为什么也不再用饼图：与旁边的「成员保费分布」饼图**图表族重复**，两块并排看着单调。
 * 堆叠条把整条铺满 100%，只表达占比（不存在"未完成"的读法），
 * 且与饼图属于不同视觉语言，两块并排层次分明。
 *
 * @param {HTMLElement} container 容器
 * @param {Array<{label:string,value:number,color?:string,display?:string,sub?:string}>} data
 * @param {{emptyText?:string}} [options]
 */
export function renderStackedBar(container, data, options = {}) {
  if (!container) return;
  const items = normalize(data).filter((d) => d.value > 0);
  if (!items.length) {
    container.innerHTML = `<div class="chart-empty">${escapeHtml(options.emptyText || '暂无数据')}</div>`;
    return;
  }

  const total = items.reduce((sum, d) => sum + d.value, 0);

  const segments = items
    .map((d) => {
      const pct = total ? ((d.value / total) * 100).toFixed(1) : '0.0';
      // flex-grow = 数值 → 各段宽度按占比分配；整条始终铺满 100%
      return `<span style="flex:${d.value};background:${d.color}" title="${escapeHtml(d.label)} ${pct}%"></span>`;
    })
    .join('');

  const legend = items
    .map((d) => {
      const pct = total ? ((d.value / total) * 100).toFixed(1) : '0.0';
      const shown = d.display !== null ? d.display : d.value;
      const sub = d.sub ? `<em class="legend-sub">${escapeHtml(d.sub)}</em>` : '';
      return `<li class="legend-item">
        <span class="legend-dot" style="background:${d.color}"></span>
        <span class="legend-label">${escapeHtml(d.label)}${sub}</span>
        <span class="legend-value">${escapeHtml(shown)}</span>
        <span class="legend-pct">${pct}%</span>
      </li>`;
    })
    .join('');

  container.innerHTML = `
    <div class="stack-wrap">
      <div class="stack-bar" role="img" aria-label="占比构成">${segments}</div>
      <ul class="chart-legend">${legend}</ul>
    </div>`;
}

/**
 * 取调色板颜色。
 * @param {number} index
 * @returns {string}
 */
function palette(index) {
  return DEFAULT_COLORS[index % DEFAULT_COLORS.length];
}

/**
 * 刻度用的紧凑金额（如 4800 → `¥4,800`）：只保留整数、加千分位，避免刻度文字过长。
 * @param {number} v
 * @returns {string}
 */
function shortMoney(v) {
  const n = Math.round(Number(v) || 0);
  return '¥' + n.toLocaleString('en-US');
}

/**
 * 极坐标转直角坐标（角度以正上方为 0°，顺时针）。
 * @param {number} cx
 * @param {number} cy
 * @param {number} r
 * @param {number} angle
 * @returns {[number, number]}
 */
function polar(cx, cy, r, angle) {
  const rad = ((angle - 90) * Math.PI) / 180;
  return [cx + r * Math.cos(rad), cy + r * Math.sin(rad)];
}

/**
 * 生成一个扇形路径。
 * @param {number} cx
 * @param {number} cy
 * @param {number} r
 * @param {number} start 起始角
 * @param {number} end 结束角
 * @returns {string}
 */
function slicePath(cx, cy, r, start, end) {
  const [x1, y1] = polar(cx, cy, r, end);
  const [x2, y2] = polar(cx, cy, r, start);
  const large = end - start > 180 ? 1 : 0;
  return `M ${cx} ${cy} L ${x1} ${y1} A ${r} ${r} 0 ${large} 0 ${x2} ${y2} Z`;
}

/**
 * 归一化数据项。
 * @param {Array} data
 * @returns {Array<{label: string, value: number, color: string, display: string|null}>}
 */
function normalize(data) {
  return (data || []).map((d, i) => ({
    label: d && d.label !== undefined ? String(d.label) : '',
    value: Number(d && d.value) || 0,
    color: (d && d.color) || palette(i),
    display: d && d.display !== undefined && d.display !== null ? String(d.display) : null,
    // 待缴占比（0~1）：用于在条形内叠加「待缴段」；缺省 0 表示不叠加。
    ratio: d && d.ratio !== undefined && d.ratio !== null ? Number(d.ratio) || 0 : 0,
    // 金额下方的小字（如「待缴 ¥200.00」）；缺省 null 表示不显示。
    sub: d && d.sub !== undefined && d.sub !== null ? String(d.sub) : null,
  }));
}

/**
 * 渲染饼图（含图例与百分比）。
 * @param {HTMLElement} container 目标容器
 * @param {Array} data 数据项数组
 * @param {{emptyText?: string}} [options]
 */
export function renderPie(container, data, options = {}) {
  if (!container) return;
  const items = normalize(data).filter((d) => d.value > 0);
  if (!items.length) {
    container.innerHTML = `<div class="chart-empty">${escapeHtml(options.emptyText || '暂无数据')}</div>`;
    return;
  }

  const total = items.reduce((sum, d) => sum + d.value, 0);
  const size = 200;
  const cx = size / 2;
  const cy = size / 2;
  const r = 78;

  let angle = 0;
  const slices = items
    .map((d) => {
      const sweep = (d.value / total) * 360;
      const start = angle;
      const end = angle + sweep;
      angle = end;
      if (items.length === 1) {
        return `<circle cx="${cx}" cy="${cy}" r="${r}" fill="${d.color}"></circle>`;
      }
      return `<path d="${slicePath(cx, cy, r, start, end)}" fill="${d.color}" stroke="#ffffff" stroke-width="1.5"></path>`;
    })
    .join('');

  const legend = items
    .map((d) => {
      const pct = total ? ((d.value / total) * 100).toFixed(1) : '0.0';
      const shown = d.display !== null ? d.display : d.value;
      // sub：标签下的补充说明（如「待缴 ¥200.00」），用于在不破坏占比语义的前提下保留待缴信息
      const sub = d.sub ? `<em class="legend-sub">${escapeHtml(d.sub)}</em>` : '';
      return `<li class="legend-item">
        <span class="legend-dot" style="background:${d.color}"></span>
        <span class="legend-label">${escapeHtml(d.label)}${sub}</span>
        <span class="legend-value">${escapeHtml(shown)}</span>
        <span class="legend-pct">${pct}%</span>
      </li>`;
    })
    .join('');

  container.innerHTML = `
    <div class="pie-wrap">
      <svg class="pie-svg" viewBox="0 0 ${size} ${size}" width="${size}" height="${size}" role="img" aria-label="饼图">${slices}</svg>
      <ul class="chart-legend">${legend}</ul>
    </div>`;
}

/**
 * 渲染横向柱状图。
 * @param {HTMLElement} container 目标容器
 * @param {Array} data 数据项数组
 * @param {{emptyText?: string}} [options]
 */
export function renderBar(container, data, options = {}) {
  if (!container) return;
  const items = normalize(data);
  if (!items.length) {
    container.innerHTML = `<div class="chart-empty">${escapeHtml(options.emptyText || '暂无数据')}</div>`;
    return;
  }

  const max = Math.max(...items.map((d) => d.value), 1);
  const rowH = 26;
  const gap = 12;
  const labelW = 76;
  // chartW 取大一些：SVG 按 viewBox 等比缩放到容器宽度，条形区太短会让图表在面板里显得局促，
  // 也会压缩各条之间的长度差（对比不直观）。
  const chartW = 300;
  const valueW = 112;
  const rowsH = items.length * (rowH + gap);
  const axisH = 22;

  // 刻度轴 + 极浅网格线 —— 让「这是一根测量刻度」这件事显式可见。
  // 关键：条尾右侧的空白因此被读成"刻度区内的剩余量"，而不是"没填满的进度"。
  const TICKS = 4;
  let axis = `<line x1="${labelW}" y1="${rowsH}" x2="${labelW + chartW}" y2="${rowsH}" stroke="#e3e8ef" stroke-width="1"></line>`;
  for (let i = 0; i <= TICKS; i++) {
    const x = labelW + Math.round((chartW * i) / TICKS);
    const anchor = i === 0 ? 'start' : i === TICKS ? 'end' : 'middle';
    axis += `<text x="${x}" y="${rowsH + 15}" class="bar-tick" text-anchor="${anchor}">${escapeHtml(shortMoney((max * i) / TICKS))}</text>`;
    if (i > 0 && i < TICKS) {
      axis += `<line x1="${x}" y1="0" x2="${x}" y2="${rowsH}" stroke="#eef2f7" stroke-width="1" stroke-dasharray="3 4"></line>`;
    }
  }

  const bars = items
    .map((d, i) => {
      const y = i * (rowH + gap);
      const w = d.value > 0 ? Math.max(2, Math.round((d.value / max) * chartW)) : 0;
      const shown = d.display !== null ? d.display : d.value;
      // 待缴段：以「本行整条长度」为基准按 ratio 取一段，**锚定左侧**（各行左对齐，便于横向比较）。
      // 待缴段用「本条自身颜色 + 半透明黑」加深，而不是另取一个颜色：
      // 调色板有 10 色（蓝/绿/琥珀/橙/紫/青/红/靛/绿/橙），任何新色都可能与其中某个撞色
      // （实测医疗险 #f59f00 与琥珀色 #f59e0b 几乎一致）。同色加深则永不撞色，
      // 且语义直白：**这条里颜色更深的部分 = 近 60 天待缴**。白色细线用于让分界更利落。
      const ratio = Math.max(0, Math.min(1, Number(d.ratio) || 0));
      const pendingW = w > 0 && ratio > 0 ? Math.max(2, Math.round(w * ratio)) : 0;
      const hasSub = Boolean(d.sub);
      const pendingRect = pendingW
        ? `<rect x="${labelW}" y="${y}" width="${pendingW}" height="${rowH}" fill="#000" fill-opacity="0.34"></rect>` +
          (pendingW < w - 1
            ? `<rect x="${labelW + pendingW - 1}" y="${y}" width="2" height="${rowH}" fill="#fff" fill-opacity="0.85"></rect>`
            : '')
        : '';
      // ⚠️ 直角条（rx=2），**不用圆角胶囊**：圆角胶囊正是「进度条 / 按钮」的视觉语言，
      //    会把数据条误读成"完成度"。直角才是"数据条"。
      return `
        <text x="0" y="${y + rowH / 2 + 4}" class="bar-label">${escapeHtml(d.label)}</text>
        <rect x="${labelW}" y="${y}" width="${w}" height="${rowH}" rx="2" fill="${d.color}"></rect>
        ${pendingRect}
        <text x="${labelW + chartW + 8}" y="${hasSub ? y + rowH / 2 - 1 : y + rowH / 2 + 4}" class="bar-value">${escapeHtml(shown)}</text>
        ${hasSub ? `<text x="${labelW + chartW + 8}" y="${y + rowH / 2 + 13}" class="bar-sub">${escapeHtml(d.sub)}</text>` : ''}`;
    })
    .join('');

  container.innerHTML = `
    <svg class="bar-svg" viewBox="0 0 ${labelW + chartW + valueW} ${rowsH + axisH}" width="100%" height="${rowsH + axisH}" role="img" aria-label="柱状图">${axis}${bars}</svg>`;
}
