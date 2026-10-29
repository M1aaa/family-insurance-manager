# 缴费记录增量 — 全局一致性审查记录

| 项目 | 内容 |
|---|---|
| 审查人 | 工程师 寇豆码（software-engineer） |
| 审查对象 | 缴费记录（`paidPeriodsOverride`）+ 逾期增量（v1.2） |
| 基准 | `docs/ARCHITECTURE.md` v1.2 |
| 审查方式 | 逐文件通读实际磁盘内容 + 导入/语法自检 + 独立回归测试 |
| 最终结论 | **IS_PASS: YES** |

---

## 1. 逐项核对结论

### 1.1 实际落盘完整性 — 通过（含 2 处修复）

逐个通读以下文件，确认改动真实存在、无半截代码、无语法/缩进问题：

- 后端：`app/models.py`、`app/calc.py`、`app/schemas.py`、`app/services/policy_service.py`、
  `app/services/stats_service.py`、`app/routers/policies.py`、`app/main.py`
- 前端：`static/js/api.js`、`static/js/components/policyForm.js`、`static/js/views/dashboard.js`、
  `static/js/views/policies.js`、`static/css/styles.css`

自检命令：

```
.venv/Scripts/python.exe -c "import app.main"      # IMPORT OK
node --check <每个 .js 复制为 .mjs>                  # 11 个前端文件全部 OK
```

**核对中发现 2 处「意图已描述、实际磁盘缺失/半实现」的缺陷（详见第 2 节），已修复。**

### 1.2 跨文件约定一致性 — 通过

| 后端（snake_case） | camelCase 输出 | 前端读取位置 | 结论 |
|---|---|---|---|
| `paid_periods_override` | `paidPeriodsOverride` | `policyForm.js` / `policies.js` | ✅ |
| `expected_periods_to_date` | `expectedPeriodsToDate` | `policyForm.js` | ✅ |
| `overdue_periods` | `overduePeriods` | `policies.js` / `dashboard.js` | ✅ |
| `earliest_due_date`（`OverdueItemOut`） | `earliestDueDate` | `dashboard.js` | ✅ |
| `overdue_policy_count` | `overduePolicyCount` | `dashboard.js` | ✅ |

- `DashboardOut.overdue` 已在 `stats_service.build_dashboard` 中真正填充（非空列表）；
- `stats_service` 正确导入 `enum_value` / `Schedule` / `OverdueItemOut`；
- `api.js` 的 `payNextPeriod` 与后端 `POST /{id}/pay-next` 路径一致。

### 1.3 算法自洽性 — 通过（修复后）

`enumerate_upcoming` 起点 `max(paid_periods, k_max + 1)` 三情形实测：

| 情形 | k_max | expected | paid | overdue | upcoming 期次 | 结论 |
|---|---|---|---|---|---|---|
| 自动推算 | 62 | 63 | 63 | 0 | [64, 65] | ✅ 与旧行为一致 |
| 提前缴（override=70） | 62 | 63 | 70 | 0 | [] | ✅ 已缴期次不泄漏 |
| 逾期（override=60） | 62 | 63 | 60 | 3 | [64, 65] | ✅ 逾期期次不进窗口 |

`guard < 4` 兜底在三种情形下均够用（60 天窗口月缴最多 3 期）；起点前移只会让 `due > end` 更早触发 `break`，不增加迭代次数。

### 1.4 边界与不变量 — 通过

| 边界 | 期望 | 实测 |
|---|---|---|
| 趸交 `total=1`，override=0 | 逾期 1、未缴清、`nextDueDate=生效日` | ✅ |
| 趸交 `total=1`，override=1 | 缴清、`nextDueDate=None` | ✅ |
| 生效日在未来（`k_max=-1`） | `expected=0`、`paid=0`（不显示「已缴 1 期」） | ✅ |
| `totalPeriods=0`（交至某年龄已过） | 无除零/负数，`progress=100.0`、缴清；`pay-next` 返回 422 | ✅ |
| override 超 `totalPeriods` | `calc_schedule` 防御性钳制（`min(override, total)`） | ✅ |
| 「交至某年龄」override 上限 | 请求校验不判定（`max_periods=None`），由 calc 钳制；前端亦不预判 | ✅ 不误报 |

### 1.5 向后兼容 — 通过

- **既有 84 项测试全绿**（修复后仍全绿）；
- 对 `paid_periods_override` 为 `NULL` 的保单，`paidPeriods / cumulativePaid / nextDueDate / isPaidOff / progressPercent` 与 v1.1 旧公式**逐值相同**（新增测试 `test_override_none_is_backward_compatible` 逐值断言）；
- `_run_additive_migrations` 幂等，对既有库只做 `ADD COLUMN`，NULL 数据行为不变。

### 1.6 前端健壮性 — 通过

- `onPayClick` 事件委托挂在 `#upcoming-table` / `#overdue-table`（每次 `container.innerHTML` 重建），**未**挂在 `container` 上，重复渲染不累积监听器（`app:refresh` → `route()` 整体重渲染，旧元素被替换）；
- `WARN_ICON` 已定义（内联 SVG，无 CDN）；
- 无逾期时 `paintOverdue()` 首行 `if (!overdue.length) return;` 安全返回；
- `policies.js` 的 `fmtPercent` 已不再使用，`import` 中已清理（仅 `fmtMoney, fmtDate, escapeHtml`）。

### 1.7 文档同步 — 已完成

- `docs/ARCHITECTURE.md` 新增 §5.7「缴费记录与逾期」（含 `paidPeriodsOverride` 语义、`overduePeriods` 推导、`pay-next` 契约、看板/前端/迁移增量）；
- §3.3 `policies` 表新增 `paid_periods_override` 行；
- 「修订记录」新增 v1.2 块；文档版本号更新为 v1.2。**未重写任何既有章节。**

---

## 2. 发现的问题与修复

### 缺陷 1（严重）：`enumerate_upcoming` 起点未使用 `max(paid_periods, k_max + 1)`

- **现象**：实际磁盘为 `k = sched.k_max + 1`，与设计（v1.2）不符。提前缴场景（`override > k_max+1`）下，已缴期次被错误列入「近 60 天待缴清单」并显示「已缴」按钮。
- **复现**：月缴 20 年、生效 2020-03-15、`override=70`、today=2025-05-20 → 待缴清单返回第 64/65 期（已缴），实际下一未缴为第 71 期（2026-01-15）。
- **根因**：与主理人已修复的 `update_policy` 同类的**并行编辑竞态丢失**——设计描述中的改动未落到磁盘。
- **修复**：`app/calc.py` 改为 `k = max(sched.paid_periods, sched.k_max + 1)`，并补充注释说明三情形。**最小变更。**

### 缺陷 2（严重）：`policyForm.js` 的 `collect()` 读取了 `paidPeriodsOverride` 却未写入 payload

- **现象**：`const paidPeriodsOverride = readNumber(...)` 被赋值，但 `payload` 对象中**缺失**该字段，也无对应校验。→ 用户在表单填写的「已缴期数」在新增/编辑时被**静默丢弃**，需求核心入口失效。
- **修复**：`collect()` 中补回 `paidPeriodsOverride` 字段与校验（非负、≤1200；趸交 ≤1；固定年限 ≤ `paymentTermValue × 每年期数`；「交至某年龄」不预判上限），与后端 `PolicyBase` 校验对齐。**最小变更。**

> 两处缺陷均属「设计已描述、实现缺失」，符合主理人关于并行编辑丢失风险的提示。

---

## 3. 测试结果

```
.venv/Scripts/python.exe -m pytest -q
103 passed, 2 warnings in 1.82s
```

- 既有 84 项：**全绿**（零回归）；
- 新增 19 项：`tests/test_payment_record.py`，覆盖
  `due_date_for_period` 同源、向后兼容不变式、提前缴/逾期语义、
  `enumerate_upcoming` 三情形起点、趸交/未来生效/`total=0`/超限钳制边界、
  `create/update` 落库与清空、`pay-next`（推进 / 逾期优先 / 缴清 422 / 不存在 404 / `total=0` 422）、
  校验（负数/超总期数/超 1200）、看板 `overdue` 清单与 `overduePolicyCount`（camelCase 对齐）。

---

## 4. 代码摘要

### 新增文件

| 文件 | 说明 |
|---|---|
| `tests/test_payment_record.py` | 缴费记录与逾期增量回归测试（19 项） |
| `docs/review-payment-record.md` | 本审查记录 |

### 修改文件（本次审查）

| 文件 | 关键函数 / 改动 |
|---|---|
| `app/calc.py` | `enumerate_upcoming`：起点改为 `max(paid_periods, k_max + 1)` |
| `static/js/components/policyForm.js` | `collect()`：补回 `paidPeriodsOverride` 字段 + 校验 |
| `docs/ARCHITECTURE.md` | 新增 §5.7、§3.3 列行、修订记录 v1.2、版本号 v1.2 |

### 已核对无需改动（主理人既有实现，确认正确）

- `app/models.py`（`paid_periods_override` 列）
- `app/calc.py`（`due_date_for_period`、`calc_schedule` 双路径、`Schedule` 新字段）
- `app/schemas.py`（`PolicyBase` 校验器、`PolicyOut`/`OverviewOut`/`OverdueItemOut`/`DashboardOut` 新字段）
- `app/services/policy_service.py`（`_to_out`、`create/update` 落库、`pay_next_period`）
- `app/services/stats_service.py`（`overdue` 清单、`overdue_policy_count`）
- `app/routers/policies.py`（`POST /{id}/pay-next`）
- `app/main.py`（`_ADDITIVE_COLUMNS` + `_run_additive_migrations`）
- `static/js/api.js`、`static/js/views/dashboard.js`、`static/js/views/policies.js`、`static/css/styles.css`

---

## 5. 结论

**IS_PASS: YES** — 2 处严重缺陷已修复，既有 84 项 + 新增 19 项测试全绿，向后兼容不变式成立，文档已同步。
