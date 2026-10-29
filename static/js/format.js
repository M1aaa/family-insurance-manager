/**
 * 纯格式化工具模块（叶子模块，零依赖）。
 *
 * 本模块**不导入任何其他模块**，因此可被任意视图 / 组件安全引用，绝不会产生
 * 循环依赖。除金额、日期、期次、百分比等纯格式化函数外，另提供少量无副作用的
 * DOM 文本工具（`escapeHtml`、`todayStr`），便于各视图安全拼接 HTML 字符串。
 *
 * 约定（见 ARCHITECTURE.md 10.1）：日期展示 `YYYY-MM-DD`；金额 `¥` + 千分位 + 2 位小数；
 * 期次展示「第 {k+1}/{total} 期」（1 基）；百分比 1 位小数。
 */

/**
 * 金额格式化：`17000` → `"¥17,000.00"`；非法 / 空值按 0 处理。
 * @param {number|string|null|undefined} value
 * @returns {string}
 */
export function fmtMoney(value) {
  const n = Number(value);
  const safe = Number.isFinite(n) ? n : 0;
  const negative = safe < 0;
  const fixed = Math.abs(safe).toFixed(2);
  const [intPart, decPart] = fixed.split('.');
  const grouped = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return `${negative ? '-' : ''}¥${grouped}.${decPart}`;
}

/**
 * 整数格式化（带千分位），用于保单数 / 笔数等计数。
 * @param {number|string|null|undefined} value
 * @returns {string}
 */
export function fmtInt(value) {
  const n = Number(value);
  const safe = Number.isFinite(n) ? Math.round(n) : 0;
  return String(safe).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
}

/**
 * 文件大小格式化（1024 进制、1 位小数；整数值省略小数）。
 * `0` → `"0B"`；`348160` → `"340KB"`；`1258291` → `"1.2MB"`。
 * @param {number|string|null|undefined} bytes
 * @returns {string}
 */
export function fmtFileSize(bytes) {
  const n = Number(bytes);
  const safe = Number.isFinite(n) && n > 0 ? n : 0;
  if (safe < 1024) return `${Math.round(safe)}B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let value = safe / 1024;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  const fixed = value.toFixed(1);
  const trimmed = fixed.endsWith('.0') ? fixed.slice(0, -2) : fixed;
  return `${trimmed}${units[index]}`;
}

/**
 * 日期格式化：ISO 字符串 / 日期对象 → `"2025-05-20"`；空值 → `"—"`。
 * @param {string|Date|null|undefined} value
 * @returns {string}
 */
export function fmtDate(value) {
  if (!value) return '—';
  const text = value instanceof Date ? value.toISOString() : String(value);
  return text.length >= 10 ? text.slice(0, 10) : text;
}

/**
 * 期次格式化：`(64, 240)` → `"第 64/240 期"`；`total` 为空 / 0 → `"—"`。
 * @param {number} period 1 基的期次序号（内部 k + 1）
 * @param {number} total 总期数
 * @returns {string}
 */
export function fmtPeriod(period, total) {
  if (!total) return '—';
  return `第 ${period}/${total} 期`;
}

/**
 * 百分比格式化：`26.34` → `"26.3%"`；非法值按 0 处理。
 * @param {number|string|null|undefined} value
 * @returns {string}
 */
export function fmtPercent(value) {
  const n = Number(value);
  const safe = Number.isFinite(n) ? n : 0;
  return `${safe.toFixed(1)}%`;
}

/**
 * 返回本地当天日期字符串 `YYYY-MM-DD`（与服务端 `date.today()` 口径一致）。
 * @returns {string}
 */
export function todayStr() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/**
 * HTML 转义，防止用户输入破坏页面结构（XSS 防护）。
 * @param {*} value
 * @returns {string}
 */
export function escapeHtml(value) {
  if (value === null || value === undefined) return '';
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/**
 * 从 API 下发的枚举列表中取默认值（**禁止前端硬编码枚举值**，见项目硬约束）。
 *
 * 枚举列表来自 `GET /api/meta/enums`（`app/enums.py` 为唯一真源）。本函数保证：
 * - 返回值**必为列表内真实存在的值**（或列表为空时退化为 `''`，绝不抛异常）；
 * - 不出现任何字面量枚举值。
 *
 * `index` 用于「业务默认值不是列表首项」的少数场景（如缴费期限默认「固定年限」
 * 在后端枚举中排第 2 位）；越界时安全回退到首项。
 *
 * @param {string[]|null|undefined} list API 下发的枚举值数组
 * @param {number} [index] 期望位置（默认 0 = 首项）
 * @returns {string}
 */
export function enumDefault(list, index = 0) {
  if (!Array.isArray(list) || list.length === 0) return '';
  return list[index] !== undefined ? list[index] : list[0];
}

/**
 * 被保人姓名列表的**紧凑展示**：≤2 全列 / ≥3 截断（v1.3）。
 *
 * 例：`["张三"]` → `"张三"`；`["张三","李四"]` → `"张三、李四"`；
 * `["张三","李四","小张"]` → `"张三、李四 等 3 人"`；空 → `"—"`。
 * @param {string[]|null|undefined} names
 * @returns {string}
 */
export function fmtInsuredNames(names) {
  const list = (Array.isArray(names) ? names : []).filter(
    (name) => name !== null && name !== undefined && String(name) !== ''
  );
  if (list.length === 0) return '—';
  if (list.length <= 2) return list.join('、');
  return `${list.slice(0, 2).join('、')} 等 ${list.length} 人`;
}

/**
 * 被保人姓名**全量**文本（用于 `title` 悬浮提示）。
 * @param {string[]|null|undefined} names
 * @returns {string}
 */
export function insuredTitle(names) {
  const list = (Array.isArray(names) ? names : []).filter(
    (name) => name !== null && name !== undefined && String(name) !== ''
  );
  return list.join('、');
}
