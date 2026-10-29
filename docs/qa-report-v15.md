# QA 独立验证报告 —— 家庭保险整理工具 v1.5

> 验证人：严过关（QA 工程师）· 独立复现，**不采信工程师自测结论**
> 验证对象：v1.5（批次一 全站响应式 / 批次二 按投保人「计入家庭保费统计」）
> 版本：`app.__version__ == "1.5.0"`（已核）
> 结论：**全部靶点通过，无源码缺陷** → **路由：NoOne**

---

## 0. 结论摘要

| 靶点 | 结论 | 关键证据 |
|---|---|---|
| A 口径正确性 | ✅ 通过 | 16 条独立用例全绿；算例 A/B 逐值不变；byInsured 按**投保人**过滤；仅年化口径变化 |
| B 迁移（真实数据） | ✅ 通过 | 副本降级→迁移：列出现、存量全 =1、DDL 合规、幂等、**原库 md5 未变** |
| C 响应式（真实浏览器） | ✅ 通过 | 21/21 CDP 断言；3 视口无横向溢出；卡片 5/3+2/2 列；手机表单/弹窗/横滚/徽章正常 |
| D 回归与静态 | ✅ 通过 | `pytest` **358 passed**；15 个 JS `node --check` 全过；零外链；金额为 JSON number |

**路由判定：`路由: NoOne`（无源码 Bug；1 处测试自身缺陷已自修）**

---

## 1. 测试范围与环境

- 项目根：`<repo>`；Python：`.venv/Scripts/python.exe`
- 真实服务：`DB_PATH=<真实库副本>` + `uvicorn app.main:app --port 8123`（**原库只读，全程未改**）
- 真实浏览器：Node **v22.22.2** 内置 WebSocket 驱动系统 Chrome（`--headless=new` + CDP）
- 原库完整性：验证前后 md5 均为 `f91770ddf1c426e21633ded1ef2f91c6`（**未被改动**）

### 新增用例清单（QA 独立编写，未改任何产品代码）

| 文件 | 用例数 | 覆盖 |
|---|---|---|
| `tests/test_qa_v15_independent.py` | 16 | A1 算例 A/B、A2 投保人过滤、A3 仅年化变、A4 跨页面一致、A5 边界、D 静态契约 |
| `tests/test_qa_v15_migration_realdata.py` | 4 | B1 补列默认计入 / B2 迁移后数字不变 / B3 幂等 / B4 真实数据 A2 |
| `cdp_qa.mjs`（临时，驱动浏览器） | 21 | C 响应式 + v1.5 新增功能可见性 |

---

## 2. 靶点 A —— 口径正确性（最高优先级）

### A1 默认全计入 → 算例 A/B 逐值不变 ✅

`today = 2025-05-20`，独立重算并断言（`test_qa_v15_independent.py`）：

| 指标 | 算例 A 实测 | 算例 B 实测 |
|---|---|---|
| `annualTotalPremium` | **17000.00** ✅ | **14400.00** ✅ |
| `unpaidAnnualPremium` | **17000.00** ✅ | **2400.00** ✅ |
| `byInsured 张三` count/annual/paid | 2 / **6000.00** / **81500.00** ✅ | 2 / **2400.00** / 50000.00 ✅ |
| `byInsured 李四` / `王五` | 1 / **8000.00** / 32000.00 ✅ | 1 / **0.00** / 60000.00 ✅ |
| `byInsured 小张` | 1 / **3000.00** / 3000.00 ✅ | — |
| `byType` | 医疗 8000 → 重疾 6000 → 意外 3000 ✅ | — |
| `byPolicyholder 张三` | count 4 / annual **17000.00** ✅ | — |
| 不变式② Σ byInsured.annual == unpaidAnnualPremium | ✅ | ✅ |
| upcoming 顺序 | 06-01 → 06-15 → 07-15 ✅ | 0 笔 ✅ |

### A2 `byInsured` 按「保单投保人」而非被保人过滤 ✅（★最易做错）

自建：`张三(投保人) 给 李四(被保人) 买的保单 p_cross`。排除张三后：

- 该保单年化**从李四那一行也消失**（李四 3500 → **1500**）✅
- 反向验证：排除李四不影响张三行的 p_self ✅

### A3 排除某投保人 → 只有年化口径变，其余全不变 ✅（全字段深比对）

把看板展平为「路径→值」并逐字段 diff（`test_a3_only_annual_fields_change`）：**所有变化字段均属年化口径**，且确有变化（非空跑）。

**排除前后对照表**（`张三` 为投保人、`李四` 为被保人、`p_cross` 跨两者）：

| 字段 | 排除前 | 排除后 | 变化 |
|---|---:|---:|---|
| `overview.annualTotalPremium` | 4500.0 | 1500.0 | ★变 |
| `overview.unpaidAnnualPremium` | 4500.0 | 1500.0 | ★变 |
| `byInsured.张三.annualPremium` | 1000.0 | 0.0 | ★变 |
| `byInsured.李四.annualPremium` | **3500.0** | **1500.0** | ★变（p_cross 消失） |
| `byType.重疾险 / 医疗险` | 1000 / 2000 | 0 / 0 | ★变 |
| `byType.意外险` | 1500.0 | 1500.0 | 不变 |
| `byPolicyholder.张三`（**该行仍在**，count 不变，仅年化 0） | 3000.0 | 0.0 | ★变 |
| `byPolicyholder.李四` | 1500.0 | 1500.0 | 不变 |
| `policyTotal` / `insuredPersonCount` / `upcoming60Count` | 3 / 2 / 0 | 3 / 2 / 0 | **不变** |

另：`upcoming[]`、`overdue[]`、`paidToDatePremium`、`upcoming60Premium`、各维度 `policyCount`/`activePolicyCount` **逐值不变**（`test_a3_upcoming_overdue_paid_untouched` 显式断言）。

### A4 跨页面一致性 ✅

`/api/members[].annualPremium` == `dashboard.byMember.byInsured[].annualPremium`，逐成员相等：
- 单元（TestClient）：默认全开 + 排除后 两组均一致 ✅
- **真实 HTTP**（uvicorn:8123）：默认 / 排除张三 / 还原 三态均 `consistent=True` ✅
- `coverageTypes[].annualPremium` 同样按投保人过滤，但 `count` 不变 ✅

### A5 边界 ✅

| 边界 | 结果 |
|---|---|
| ①排除**所有**投保人 | 所有年化→0；`policyTotal`/`activePolicyCount`/`insuredPersonCount`/待缴/已缴/清单**全不变** ✅ |
| ②某人**既被保人又投保人** | 仅剔除其「投保人身份」名下保单；其作为他人保单被保人的部分按他人开关决定 ✅ |
| ③开关**关掉再打开** | 看板数字**逐字段回到原值**（往返一致）✅ |
| ④新建成员默认 | `countInStats=true` ✅ |

---

## 3. 靶点 B —— 迁移（真实数据安全）

以 `data/insurance.db` **副本**降级（`ALTER TABLE members DROP COLUMN count_in_stats`）模拟 v1.4，再跑 `_run_additive_migrations()`：

1. ✅ `members` 新增 `count_in_stats`，DDL 含 **`INTEGER NOT NULL DEFAULT 1`**，**存量成员全部 = 1（计入）**
2. ✅ 迁移后 dashboard 数字 == 「不过滤基线」（把 `excluded_holder_ids` 打桩为空集 = v1.4 行为），**逐字段相等**
3. ✅ **连启 2 次幂等**：列集合 / DDL / flags / dashboard 全等
4. ✅ **原库 md5 前后不变**（`f91770dd…1c6`）——红线未触碰
5. ✅ 示例数据复验 A2：排除张三后，被保人李四的年化下降、计数不变

---

## 4. 靶点 C —— 响应式（真实浏览器 CDP，21/21）

截图目录：`docs/screenshots/v15-qa/`

| 视口 | 卡片列数（实测） | 横向溢出 | 断言 |
|---|---|---|---|
| 1440×900 | **5 张一行** `perRow=[5]` | 无（scrollW 1440 = clientW） | ✅ |
| 768×1024 | **折行 3+2，每行排满** `rowWidths=[720,720]`==容器 720 | 无 | ✅ |
| 390×844 | **2 列** `perRow=[2,2,1]` | 无 | ✅ |

三个视口 `document.documentElement.scrollWidth <= clientWidth` 均成立（**无横向溢出**）。

手机专项（390×844）：
- ✅ 新增保单表单可填（27 个可填控件，首控件高 44px），弹窗 `697px ≤ 844px` 不溢出
- ✅ 保单列表可横滚（`scrollWidth 1370 > clientWidth 356`）无裁切，且页面无横向溢出
- ✅ 成员表单「计入家庭保费统计」复选框可见、默认勾选、可点击切换
- ✅ 成员列表含「计入统计」列；保单列表 / 成员列表均正确渲染 `<span class="badge off">不计入</span>`
- ✅ 徽章出现后仍无横向溢出

---

## 5. 靶点 D —— 回归与静态

| 项 | 结果 |
|---|---|
| 全量 `pytest tests/ -q` | **358 passed**（基线 338 + QA 新增 20）≥ 333 ✅ |
| `node --check` 前端 JS | 15/15 全部通过 ✅ |
| `static/` 零外链 | 仅 `favicon.svg` 的 SVG 命名空间 `www.w3.org/2000/svg`（允许），无 CDN/unpkg/jsdelivr ✅ |
| 金额字段 JSON number | 示例服务原始 JSON：`"annualTotalPremium":6000.0` 等**无引号** ✅ |
| 版本 | `1.5.0` ✅ |

---

## 6. 遗留问题（非阻断）

1. **次要观察（cosmetic，非 v1.5 引入）**：看板「险种保费分布」表在手机端首列险种名（如「重疾险」）会折成两行。该表位于普通 `.panel` 而非 `.panel.table-panel`，未套用 `width:max-content` 规则，故按容器宽换行。**无横向溢出、文字可读**，不影响功能；如需优化可另行处理（不属本批 6 处口径 / 响应式靶点）。
2. 工程师自测称「333 passed」，实测当前基线为 **338 passed**（差异为其后追加用例所致，非缺陷）。

---

## 7. 路由判定

> **路由：`NoOne`**

- 靶点 A/B/C/D **全部通过**，未发现任何源码缺陷。
- 过程中唯一一次失败（`test_d_money_fields_are_json_numbers`）系 **QA 自身测试的偏移量 bug**（未跳过 JSON 的冒号），源码数值正确 → **路由：QA（已自修）**，非产品问题。
- 无需进入第 2 轮（本轮即全绿）。

**签核**：QA 工程师 · 严过关（Yan）· 2026-09-18
