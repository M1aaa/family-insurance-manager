# v1.7 独立验证报告（QA 严过关 / Edward）

> **结论**：**路由判定 = NoOne**。v1.7 两个正交需求（A 新增「当年未缴费金额」/ B 首页「不计入统计」筛选）经**独立重算 + 真实 HTTP + 真实浏览器**三重验证，**未发现源码缺陷**。全部靶点通过。
>
> - 基线回归：`.venv/Scripts/python.exe -m pytest tests/ -q` → **404 passed**（工程师基线 381 + 本 QA 新增 23），零失败。
> - 浏览器 E2E：**35 / 35 断言通过**，零 pageerror / console.error / 请求失败。
> - 未采信工程师任何自测数字；`yearUnpaidPremium` 14500 / 1000 由本 QA **逐单手算**重新证明。
>
> 验证方式**独立于**工程师用例：新增测试文件 `tests/test_qa_v17_independent.py`（自建独立枚举器，不复用被测函数）；E2E 脚本 `docs/qa/qa_v17_frontend_e2e.cjs`（真实 Chrome + 临时库，未触碰 `data/insurance.db`）。

---

## 0. 验证范围与证据清单

| 项 | 内容 |
|---|---|
| 规格依据 | `docs/ARCHITECTURE-v17-increment.md`（573 行）+ `docs/ARCHITECTURE.md` §5 |
| 被测版本 | `app/__init__.py = 1.7.0`；`GET /api/health.version = "1.7.0"`（实测） |
| 新增用例 | `tests/test_qa_v17_independent.py`（23 用例，全绿） |
| 浏览器脚本 | `docs/qa/qa_v17_frontend_e2e.cjs`（35 断言，全绿） |
| 迁移脚本 | `<临时目录>/qa_v17_migration_real.py`（真实 uvicorn 启动路径） |
| 截图 | `docs/screenshots/v17-qa/`（5 张） |
| 只读约束 | 全程使用临时库/副本；`data/insurance.db` 的 md5 与 mtime 验证前后**未变** |

---

## 1. 逐靶点结论（通过 / 失败 + 证据）

### A. 红线：默认全部计入 → 既有数字逐值不变 ✅ 通过

用 `tests/conftest.py` 的 `seed_example_a` / `seed_example_b`（种子真源）自建，逐值断言（`test_A_example_a_all_values` / `test_A_example_b_all_values`）。

**算例 A（today=2025-05-20）实测**

| 指标 | 期望（规格 §8.2） | 实测 | 结论 |
|---|---|---|---|
| annualTotalPremium | 17000 | 17000.00 | ✅ |
| unpaidAnnualPremium | 17000 | 17000.00 | ✅ |
| upcoming60Premium | 4000 | 4000.00 | ✅ |
| upcoming60Count | 3 | 3 | ✅ |
| policyTotal | 4 | 4 | ✅ |
| activePolicyCount | 3 | 3 | ✅ |
| insuredPersonCount | 3 | 3 | ✅ |
| byInsured 张三 / 李四 / 小张 | 6000 / 8000 / 3000 | 6000 / 8000 / 3000 | ✅ |
| 张三 policyCount / paidToDatePremium | 2 / 81500 | 2 / 81500.00 | ✅ |

**算例 B（today=2025-05-20）实测**

| 指标 | 期望 | 实测 | 结论 |
|---|---|---|---|
| annualTotalPremium | 14400 | 14400.00 | ✅ |
| unpaidAnnualPremium | 2400 | 2400.00 | ✅ |
| upcoming60Premium / Count | 0 / 0 | 0.00 / 0 | ✅ |
| policyTotal / activePolicyCount / insuredPersonCount | 3 / 3 / 2 | 3 / 3 / 2 | ✅ |
| byPolicyholder 张三 / 王五 | 2400 / **12000** | 2400 / 12000 | ✅ |

> 注：`p_F` 状态确为**生效中**（仅期次已缴完），`byPolicyholder` 王五 12000 计入；`p_B` 2021-08-01 交 10 年、`p_C` 交 20 年 —— 均与规格一致。

### B. 新指标 `yearUnpaidPremium` —— 逐单手算 ✅ 通过

**独立枚举器**（`_hand_periods`）：自行按 `payment_term_type`/`payment_frequency` 推导总期数与已缴期数、自行推算 `k_max`，**不复用** `calc.enumerate_year_periods` / `calc.calc_schedule`（日期算术复用规格钦定的 `add_months`，属共享输入）。

**逐单手算 vs 接口对照表（算例 A / B，today=2025-05-20）**

| 保单 | 本年度到期且未缴的期次（手算） | 单手算金额 | 接口 `yearUnpaidPremium` |
|---|---|---|---|
| A · p_A 月缴500 | k=63..69（2025-06-15 ~ 12-15）7 期 | 3500 | — |
| A · p_B 年缴8000 | k=4（2025-08-01）1 期 | 8000 | — |
| A · p_C 年缴3000 | k=1（2025-06-01）1 期 | 3000 | — |
| A · p_D 趸交2019 | 无（2025 无期次） | 0 | — |
| **算例 A 合计** | | **14500** | **14500.00 ✅** |
| B · p_E 月缴200 | k=0..4（2025-08 ~ 12）5 期 | 1000 | — |
| B · p_F 交至60岁 | 0（2019 已缴清） | 0 | — |
| B · p_G 趸交2019 | 0 | 0 | — |
| **算例 B 合计** | | **1000** | **1000.00 ✅** |

**边界必测（全部通过）**

| # | 场景 | 结论 | 证据 |
|---|---|---|---|
| 1 | 已退保 / 已失效 | 返回 `[]`（不计入） | `test_B_edge_surrendered_and_lapsed_excluded` |
| 2 | 已缴清（期次全缴） | 0 | `test_B_edge_paid_off_zero` |
| 3 | 趸交·生效日在本年度且未来（未缴） | **计入**（金额 = 趸交保费 50000，可能大额） | `test_B_edge_single_future_effective_counted` |
| 3b | 趸交·生效日在过去（视为已缴） | 不计入 | `test_B_edge_single_past_effective_not_counted` |
| 4 | 生效日在未来（2025-08-01） | 本年度 8~12 月 5 期计入 | `test_B_edge_future_effective_counts_this_year` |
| 5 | **跨年度边界 12-31 vs 01-01** | 2024-12-31 归 2024；2025-01-01 归 2025（override=0 隔离） | `test_B_boundary_dec31_vs_jan01` |
| 6 | 已逾期未缴 | **计入**（2025 年 3~12 月共 10 期 = 1000） | `test_B_edge_overdue_unpaid_counted` |
| 7 | 不受筛选影响 | 排 m1∪医疗险 后仍 14500 | `test_B_year_unpaid_not_affected_by_filter` |

> 说明：`test_B_boundary_dec31_vs_jan01` 初版误把「已缴」自动推算与年份归属混在一起（`k < paid` 会先过滤掉 2024-12-31 期）；改用 `paid_periods_override=0` 隔离被测点后通过 —— 这是**测试自身的修正**，非源码缺陷。

### C. 筛选的过滤范围 ✅ 通过

排除投保人 m1（算例 A，m1 是全部 4 单的投保人）后**逐字段对照**：

| 字段 | 排除前 | 排除后 | 是否应变 | 结论 |
|---|---|---|---|---|
| annualTotalPremium | 17000 | **0** | ✅ 应变 | ✅ |
| unpaidAnnualPremium | 17000 | **0** | ✅ 应变 | ✅ |
| 三维 `annualPremium` | 有值 | **全 0** | ✅ 应变 | ✅ |
| `/api/members[].annualPremium`（含 `coverageTypes[].annualPremium`） | 有值 | **0** | ✅ 应变 | ✅ |
| policyTotal | 4 | 4 | ❌ 不变 | ✅ |
| activePolicyCount | 3 | 3 | ❌ 不变 | ✅ |
| insuredPersonCount | 3 | 3 | ❌ 不变 | ✅ |
| 各维度 policyCount / activePolicyCount | — | 不变 | ❌ 不变 | ✅ |
| upcoming60Premium / Count | 4000 / 3 | 4000 / 3 | ❌ 不变 | ✅ |
| upcoming[] | 3 条 | 3 条（完全相同） | ❌ 不变 | ✅ |
| overdue[] | [] | [] | ❌ 不变 | ✅ |
| 各维度 paidToDatePremium | — | 不变 | ❌ 不变 | ✅ |
| **yearUnpaidPremium** | 14500 | **14500** | ❌ **不变**（★ 设计明确） | ✅ |

排除险种「医疗险」后（`test_C_exclude_type_scope`）：`annualTotalPremium 17000→9000`；`byType[医疗险].annualPremium 8000→0` 而 `policyCount=1` / `paidToDatePremium` 不变；`yearUnpaidPremium 14500` / `upcoming60Premium 4000` 不变。✅

**并集不重复扣减**（`test_C_union_no_double_deduction`，自建 3 单）：基线 18000 → 仅排 m1 → 4000 → 仅排重疾险 → 8000 → **同时排 m1 ∪ 重疾险 → 0（命中两维的 p1 只扣一次，且不为负）**，policyTotal 恒为 3。✅

### D. ★★ 编辑成员不得把已排除成员重置为「计入」 ✅ 通过（端到端）

- **后端裸行为（记录陷阱，`test_D_backend_raw_put_without_flag_resets`）**：`PUT /api/members/{id}` **省略** `countInStats` → `MemberUpdate.count_in_stats` 默认 `True` → 该成员被**重置为计入**。这是**设计保留**的兼容行为（规格 §1.8 明确「保留 MemberCreate.countInStats 字段」）。
- **前端回填（`test_D_frontend_payload_backfill_preserves`）**：模拟 `members.js` 实际提交的 `{...form, countInStats: member.countInStats !== false}` → `countInStats` 保持 `false`，看板 `annualTotalPremium` 保持 0。
- **真实浏览器端到端（E2E 断言）**：
  1. 首页取消勾选「张三」→「存为默认」→ `GET /api/stats/exclusions` 含张三 id ✅
  2. 进入成员页 → 「统计状态」列显示「**不计入**」✅
  3. 打开编辑弹窗 → 改备注「QA改过备注」→ 保存
  4. `GET /api/stats/exclusions` **仍含张三** ✅
  5. `GET /api/members` → 张三 `countInStats === false` ✅（且备注确已落库，证明编辑真实生效）
  6. 回到看板 → 卡片① 仍为 **¥12,000.00**（未被悄悄改回）✅

**结论**：工程师的前端回填对策**端到端有效**。⚠️ 见「遗留问题 2」的脆弱性提示。

### E. 接口契约 ✅ 通过

| 项 | 结论 | 证据 |
|---|---|---|
| `GET /api/stats/exclusions` 结构 | 恰为 `{excludedPolicyholderIds, excludedInsuranceTypes}` | `test_E_get_structure` |
| `PUT` 全量替换 | 先 `["车险","重疾险"]` 再 `["车险"]` → 结果**只剩车险** | `test_E_full_replace_semantics` |
| 422 校验（不存在的成员 id / 非法险种） | 均 422 且 `code == "VALIDATION_ERROR"`（**不新增错误码**），失败无副作用 | `test_E_422_validation` |
| 金额字段为 JSON number | 原始 JSON 含 `"yearUnpaidPremium":14500.0`（**无引号**），且 `"yearUnpaidPremium":"` 不存在 | `test_E_year_unpaid_is_json_number` |

### F. 新表在既有库上自动创建（升级行为）✅ 通过

- **单测**（`test_F_new_table_autocreated_on_existing_db`）：真实库副本删掉 `stats_exclusions` 模拟 v1.6 → `Base.metadata.create_all` → 表被自动创建，列 `{kind, ref_id, created_at}`、复合主键 `(kind, ref_id)`；连建两次幂等；**原库 md5 与 mtime 均未变**。
- **真实应用启动路径**（uvicorn lifespan，`qa_v17_migration_real.py`）：

```json
{
  "health_version_1": "1.7.0",
  "table_created": true,
  "dash1_annual": 8500.0,   "dash1_year_unpaid": 700.0,  "dash1_policy_total": 5,
  "dash2_annual": 8500.0,   "idempotent": true,
  "orig_md5_unchanged": true, "orig_mtime_unchanged": true
}
```

→ ① 新表自动创建 ✅ ② 迁移后数字与基线一致（空表 = 零排除）✅ ③ 连启两次幂等 ✅ ④ 原库未被改动（md5 + 时间戳）✅

### G. 前端（真实浏览器 CDP）✅ 通过（35 / 35 断言）

| 靶点 | 结论 | 证据（截图 / 断言） |
|---|---|---|
| 6 张卡排布：桌面 3×2 / 平板 2×3 / 手机 2×3，无孤行 | ✅ | 行分布实测 `[3,3]` / `[2,2,2]` / `[2,2,2]` |
| 任何视口无横向溢出（`scrollWidth ≤ clientWidth`） | ✅ | 1440/1440、768/768、390/390 |
| 卡片标签顺序 | ✅ | `['当年应缴总保费','年化保费合计','当年未缴费金额','近 60 天待缴','保单总数','在保人数']` |
| 筛选面板勾选/取消 → 卡片实时变化 | ✅ | 取消张三 → 卡片① `29000 → 12000` |
| 临时筛选期间 **网络请求数 = 0** | ✅ | 拦截 `page.on('request')`：`/api` 请求数 = 0 |
| 临时筛选刷新即重置 | ✅ | 刷新后卡片① 回 29000、复选框恢复勾选 |
| 「存为默认」→ 硬刷新后仍生效 | ✅ | 硬刷新后卡片① = 12000、复选框未勾选 |
| 成员表单**无**「计入家庭保费统计」开关 | ✅ | 弹窗文本 + 无 `input[name=countInStats]` |
| 成员列表只读「统计状态」列 | ✅ | 表头含「统计状态」，张三行「不计入」 |
| 手机筛选面板可用、不溢出 | ✅ | 390 端 chips=14、面板宽 358 < 390；点击生效（卡片① 12000→0） |
| 无 JS 异常 | ✅ | pageerror=0、console.error=0、真实请求失败=0 |

**截图证据（`docs/screenshots/v17-qa/`）**

| 文件 | 内容 |
|---|---|
| `dashboard-1440x900.png` | 桌面 3×2，②③ accent 描边，筛选面板，分布图 |
| `dashboard-768x1024.png` | 平板 2×3 |
| `dashboard-390x844.png` | 手机 2×3 |
| `dashboard-390x844-filtered.png` | 手机端筛选后（①② 归 0，③ 仍 1500） |
| `members-390x844.png` | 成员页手机端 |

### H. 回归与静态 ✅ 通过

| 项 | 结论 |
|---|---|
| 全量 `pytest tests/ -q` | **404 passed**（381 基线 + 23 本 QA 新增），零失败 |
| `node --check` 全部前端 JS | 15/15 OK（无语法错误） |
| 注释块完整性（防 `node --check` 漏报） | `/*` 与 `*/` 计数逐文件配对，无 MISMATCH；`dashboard.js` / `members.js` / `memberForm.js` 全量回读确认注释未被切坏 |
| `static/` 零外链 | 唯一命中为 `favicon.svg` 的 SVG 命名空间 `http://www.w3.org/2000/svg`（非外部资源），合规 |
| 版本号 1.7.0 | `app/__init__.py = "1.7.0"`；`/api/health.version = "1.7.0"` |

---

## 2. 新增用例清单（`tests/test_qa_v17_independent.py`，23 用例）

| 分组 | 用例 |
|---|---|
| A 红线 | `test_A_example_a_all_values`、`test_A_example_b_all_values` |
| B 手算 | `test_B_hand_vs_api_example_a`、`test_B_hand_vs_api_example_b` |
| B 边界 | `test_B_edge_surrendered_and_lapsed_excluded`、`test_B_edge_paid_off_zero`、`test_B_edge_single_future_effective_counted`、`test_B_edge_single_past_effective_not_counted`、`test_B_edge_future_effective_counts_this_year`、`test_B_edge_overdue_unpaid_counted`、`test_B_boundary_dec31_vs_jan01`、`test_B_year_unpaid_not_affected_by_filter` |
| C 过滤 | `test_C_exclude_holder_scope`、`test_C_exclude_type_scope`、`test_C_member_api_annual_respects_type`、`test_C_union_no_double_deduction` |
| D 编辑 | `test_D_backend_raw_put_without_flag_resets`、`test_D_frontend_payload_backfill_preserves` |
| E 契约 | `test_E_get_structure`、`test_E_full_replace_semantics`、`test_E_422_validation`、`test_E_year_unpaid_is_json_number` |
| F 迁移 | `test_F_new_table_autocreated_on_existing_db` |

---

## 3. 遗留问题 / 观察项（均**非** v1.7 阻塞项）

1. **文档层面**：`README.md` 仍引用 `family-insurance-manager-1.6.0-amd64.tar` 等 dist 产物名（第 87/89/180 行）。`app/__init__.py` 与 `/api/health` 均已是 **1.7.0**，不属代码缺陷；建议随发布更新文档。
2. **隐式契约脆弱性（建议后续加固，非缺陷）**：后端 `MemberUpdate` 继承 `MemberCreate`，`count_in_stats` 默认 `True` → **任何绕过前端**的 `PUT /api/members/{id}`（直接调 API / 旧脚本 / 未来新前端）若省略该字段，会**静默**把已排除成员改回「计入」。当前由 `static/js/views/members.js` 的 `openEdit` 回填现值规避，且已通过真实浏览器端到端验证**有效**。为从根上消除陷阱，建议后续把 `MemberUpdate.count_in_stats` 改为 `Optional[bool] = None`（`None` = 不改），使「编辑成员」语义与「统计范围筛选」彻底解耦。
3. **覆盖缺口提示（非缺陷）**：前端临时筛选的**多被保人分摊**重算（`allocateAmountsCents` / `canonicalInsuredOrder`）本次 E2E 仅覆盖**单被保人**场景；后端多被保人分摊由既有单测覆盖。前端镜像逻辑经代码审阅与后端算法逐行比对一致，但未做浏览器级多被保人用例。

---

## 4. 路由判定

> **路由：NoOne** —— 全部靶点（A–H）通过，无源码缺陷、无测试缺陷遗留。`tests/test_qa_v17_independent.py`（23 用例）与 `docs/qa/qa_v17_frontend_e2e.cjs`（35 断言）全绿；全量回归 **404 passed**。v1.7 可交付。
