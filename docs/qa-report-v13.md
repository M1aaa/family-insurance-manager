# QA 独立验证报告 — 家庭保险整理工具 v1.3

| 项目 | 内容 |
|---|---|
| 验证人 | 严过关（Yan）· QA 工程师 |
| 验证对象 | v1.3 增量（R-39 ~ R-46，4 项新功能） |
| 验证方式 | **独立重算 + 真实 HTTP + 真实无头 Chrome**（不采信工程师自测结论） |
| 基准 | `docs/ARCHITECTURE.md` §5.6 算例集 A/B/C（`today=2025-05-20`）、`docs/ARCHITECTURE-v13-increment.md` §6.1/§7/§8、`docs/PRD-v13-increment.md` |
| 环境 | Python 3.13 venv / 系统 Chrome（无头）/ Node 22（内置 WebSocket 驱动 CDP） |
| 结论 | **全量 305 passed**（基线 245 + QA 新增 60）；浏览器 18/18 通过 |
| **智能路由判定** | **`路由: NoOne`**（未发现源码 Bug） |

---

## 1. 测试范围

1. **算例集 A/B/C 逐值独立重算**（不引用工程师断言，直接对 §5.6 期望值核对）。
2. **分摊算法 `calc.allocate_amounts` 边界攻击**（恒等式、尾差归属、异常输入）。
3. **多被保人写路径边界**（缩减集合、换主被保人、删除去重、422 格式）。
4. **真实库副本回归**（`data/insurance.db` 拷贝，**原库未改动**）。
5. **前端真实浏览器交互**（无头 Chrome 驱动，非 `node --check`）。
6. **静态资源零外部依赖 / 枚举单一真源**。

> 运行命令：`.venv/Scripts/python.exe -m pytest tests/ -q` → **305 passed, 2 warnings**。

### 新增 QA 用例清单（未改动 `app/`、`static/`）

| 文件 | 用例数 | 覆盖 |
|---|---|---|
| `tests/test_qa_v13_calc.py` | 40 | 不变式 `Σ==money(total)`（5 total × 6 n）、`n=1` 恒等、`n<=0`→`[]`、尾差归末位、0/负数/大值、类型规整 |
| `tests/test_qa_v13_stats.py` | 12 | 尾差归属（**打乱插入顺序**）、卡片/byType/byPolicyholder 不分摊、`policyCount` 全额、`insuredPersonCount` 去重、**算例 A/B/C 逐值** |
| `tests/test_qa_v13_api.py` | 8 | 2→1 清理、换主被保人双写、删除计数去重、422 格式、any-of 筛选、`分红险` 下发与建单 |

---

## 2. 逐靶点结论

### 靶点 1 — 算例集 A/B/C 独立重算 ✅ 通过

**算例集 A**（`today=2025-05-20`）实测（`test_example_a_independent_recompute`）：

| 指标 | 期望（§5.6） | 实测 | 结论 |
|---|---|---|---|
| 年度总保费 | 17000.00 | 17000.00 | ✅ |
| 近 60 天待缴 | 4000.00 / 3 笔 | 4000.00 / 3 | ✅ |
| 保单总数 / 生效中 / 在保人数 | 4 / 3 / 3 | 4 / 3 / 3 | ✅ |
| byInsured 张三 | count 2, annual 6000, upcoming 1000, **paid 81500** | 2 / 6000 / 1000 / 81500 | ✅ |
| byInsured 李四 | count 1, annual 8000, upcoming 0, paid 32000 | 1 / 8000 / 0 / 32000 | ✅ |
| byInsured 小张 | count 1, annual 3000, upcoming 3000, paid 3000 | 1 / 3000 / 3000 / 3000 | ✅ |
| byType 排序 | 医疗险8000 → 重疾险6000 → 意外险3000 | 完全一致 | ✅ |
| upcoming 顺序 | 06-01(小张)→06-15(张三)→07-15(张三) | 完全一致 | ✅ |
| byPolicyholder（m1） | 不分摊（4 张 / 17000 / 116500） | 4 / 17000.00 / 116500.00 | ✅ |

**算例集 B** 实测（`test_example_b_independent_recompute`）：

| 指标 | 期望 | 实测 | 结论 |
|---|---|---|---|
| 年度总保费 | 2400.00 | 2400.00 | ✅ |
| 待缴 / 笔数 | 0.00 / 0 | 0.00 / 0 | ✅ |
| 保单 / 生效中 / 在保 | 3 / 3 / 2 | 3 / 3 / 2 | ✅ |
| 张三 | count 2, annual 2400, paid 50000 | 2 / 2400 / 50000 | ✅ |
| 王五 | count 1, annual 0, paid 60000 | 1 / 0 / 60000 | ✅ |

**算例集 C**（5 条边界，`test_example_c_boundaries`，直算 `calc_schedule`）：

| # | 场景 | 期望 `k_max`/`paid`/`nextDue` | 实测 | 结论 |
|---|---|---|---|---|
| C1 | 月末钳制，today=2020-04-30 | 2 / 3 / 2020-04-30 | 2 / 3 / 2020-04-30 | ✅ |
| C2 | 闰年月末，today=2020-02-29 | 0 / 1 / 2020-02-29 | 0 / 1 / 2020-02-29 | ✅ |
| C3 | 精确周年日，today=2025-05-15 | 61 / 62 / 2025-05-15 | 61 / 62 / 2025-05-15 | ✅ |
| C4 | 年缴缴费日=今天 | 3 / 4 / 2025-08-01 | 3 / 4 / 2025-08-01 | ✅ |
| C5 | 生效日=今天 | -1 / 0 / 2020-03-15 | -1 / 0 / 2020-03-15 | ✅ |

**顶部卡片 / byType / byPolicyholder 与 v1.2 逐值相同**：多被保人场景下仍为**全额**（`test_card_bytype_bypolicyholder_not_allocated`）→ ✅ 未发生错误分摊。

### 靶点 2 — 分摊算法边界 ✅ 通过

- **不变式** `Σ shares == money(total)`：对 `total ∈ {0,1,100,999.99,123456789.99}` × `n ∈ {1,2,3,7,100,1000}` **共 30 组全部成立**（`test_invariant_sum_equals_money_total`）。
- `n == 1` 恒等返回 `[money(total)]` ✅；`n <= 0` → `[]` ✅；`total` 为负 → 不崩溃且仍守恒 ✅。
- **尾差归属**：`1000/3 → [333.33,333.33,333.34]` ✅；`100/7 → 前 6 份 14.29，末份 14.26` ✅。

### 靶点 3 — 前端真实浏览器验证 ✅ 通过（18/18）

> 用 **Node 22 内置 WebSocket + 系统 Chrome `--headless=new`** 直连 CDP 驱动真实浏览器；非语法检查。证据见 §4 与截图 `docs/screenshots/v13/`。

| # | 断言 | 结果 | 证据 |
|---|---|---|---|
| 1 | 列表被保人列 ≤2 全列 | ✅ | `"张三、李四"` / title 全量 |
| 2 | 列表被保人列 ≥3 截断 + title 全量 | ✅ | `"张三、李四 等 3 人"` / title `"张三、李四、小张"` |
| 3 | 电子保单列 N=0「—」不可点 | ✅ | 文本 `"—"`，无按钮 |
| 4 | 电子保单列 N=1 📎1 | ✅ | 按钮 `📎 1` |
| 5 | 电子保单列 N=2 📎2 | ✅ | 按钮 `📎 2` |
| 6 | 筛选 any-of（小张→仅 P2） | ✅ | `["P2三人被保"]` |
| 7 | 筛选 any-of（张三→P1/P2/P4） | ✅ | 3 行 |
| 8 | N=1 点击 → `window.open(inline)` | ✅ | URL 含 `/content?disposition=inline` |
| 9 | N=1 inline URL 真实返回 PDF | ✅ | HTTP 200 / `application/pdf` / 69B |
| 10 | N≥2 → 弹选择层（2 项 + 查看/下载） | ✅ | 标题含「电子保单」，每项 inline 查看 + attachment 下载 |
| 11 | 成员页保障类型 chip 展示 | ✅ | 张三 `[重疾险×1, 医疗险×1, 寿险×1]` |
| 12 | 成员页无保障显示「暂无保障」 | ✅ | 赵六 `chip-empty` |
| 13 | 点击 chip 展开明细 | ✅ | 明细含「张数 / 生效中 / 年化」 |
| 14 | 表单被保人复选框 + radio（初始全禁用未选） | ✅ | 5 cb / 5 radio / 0 选中 / 5 禁用 |
| 15 | 勾选后 radio 可选且自动成为主被保人 | ✅ | disabled=false, checked=true |
| 16 | **取消勾选主被保人 → 自动转移剩余首位** | ✅ | 转移至 m1（本人），m2 radio 禁用 |
| 17 | 未选被保人保存被拦 + 提示 | ✅ | 提示「请至少选择 1 名被保人」，弹窗保持打开 |
| 18 | 多选表单保存成功 | ✅ | 弹窗关闭 + toast「保单已新增」 |

### 靶点 4 — 静态资源零外部依赖 ✅ 通过

```bash
grep -rEn "https?://" static/ | grep -v "www\.w3\.org/2000/svg"   # (empty)
grep -rEn '(src|href)\s*=\s*"https?:|url\(\s*https?:|@import' static/  # (empty)
grep -rEni "cdn\.|unpkg|jsdelivr|googleapis|bootstrap|jquery" static/  # (empty)
```
`requirements.txt` **无新增依赖**。

### 靶点 5 — 枚举单一真源 ✅ 通过（含 1 处低危观察）

- `/api/meta/enums` 返回 `insuranceType` 共 10 值，`分红险` 位于 `年金险` 之后（`test_meta_enums_includes_dividend`）。
- 新建「分红险」保单 **201 成功**（零迁移，`test_create_dividend_policy_succeeds`）。
- 前端下拉选项全部来自 `enums.insuranceType`；**唯一硬编码**为 `policyForm.js:152` 的默认选中值 `'重疾险'`（见 §5 遗留问题 L1，非功能缺陷）。

### 靶点 6 — 多被保人写路径边界 ✅ 通过

| 攻击 | 断言 | 结果 |
|---|---|---|
| 2 被保人 → 1 被保人 | 关联表**无残留**（`{m1,m2}` → `{m1}`），GET 回读 `[m1]` | ✅ |
| 缩为单选后再筛 m2 | 不再命中（无幽灵保单） | ✅ |
| 更换主被保人 m1→m2 | `policies.insured_id==m2` **且**关联表仍 `{m1,m2}` | ✅ |
| 同人既投保人又被保人（同单） | 引用计数**去重 = 1**（非 2） | ✅ |
| 跨保单去重 | m1 在 p1（投保+被保）、p2（被保）→ **= 2** | ✅ |
| `primaryInsuredId ∉ insuredIds` | 422 `VALIDATION_ERROR`，格式 `{code,message,detail}` | ✅ |
| 成员不存在 | 422，`detail.memberId=m_nope` / `detail.role=被保人` | ✅ |
| 空被保人 | 422 `VALIDATION_ERROR` | ✅ |
| any-of 筛选（真实 HTTP） | m1→2 张、m2→1 张、m3→0 张 | ✅ |

### 靶点 7 — 真实库副本回归 ✅ 通过

原库 `data/insurance.db` **保持未迁移**（`has policy_insureds: False`，2 policies / 1 member）——未被动过。
对其**副本**起真实服务（`DB_PATH=<copy>`，版本 `1.3.0`）：

| 校验 | 结果 |
|---|---|
| 启动建 `policy_insureds` + 回填 | ✅ 2 行 |
| 每张保单 `insuredIds` 长度 = 1 | ✅ |
| `primaryInsuredId` == 迁移前 `insured_id` | ✅ |
| 保费 / 状态 / 生效日不变 | ✅ |
| 卡片 / byInsured / byType 与单人语义一致 | ✅ |
| **连续 3 次启动幂等**（行数恒为 2） | ✅ |

> 附注：首轮核对时我误将「DB 整数分」直接与「API 元」比较而报红，经查为 **QA 测试自身的单位错误**（`Money` 以分为单位存储），修正后全部通过 —— 非源码缺陷。

---

## 3. 回归声明（§8）核对

- `tests/test_qa_v12_backend.py` 仅将版本断言 `1.2.0 → 1.3.0`（第 590 行），**未削弱其他断言**（交叉确认）。
- `tests/test_calc.py` 既有算例**未改动**。
- 全量 **305 passed**（基线 245 全绿 + 新增 60）。

---

## 4. 浏览器验证证据

- 驱动：`<临时目录>/qa_v13/browser_test.mjs`（Node 22 内置 WebSocket → Chrome CDP）。
- 结果：`===== BROWSER SUMMARY: 18/18 passed =====`。
- 截图：`docs/screenshots/v13/shot-policies.png`、`docs/screenshots/v13/shot-members.png`。

---

## 5. 遗留问题（非阻塞）

| # | 级别 | 描述 | 建议 |
|---|---|---|---|
| L1 | 低 | `static/js/components/policyForm.js:152` 将险种下拉的**默认选中值**硬编码为 `'重疾险'`。枚举列表本身来自 `/api/meta/enums`，故不影响「单一真源」与「分红险下发」；但若该枚举值被改名/移除，默认选中会静默失效。 | 可改为「取 `enums.insuranceType[0]`」以彻底去硬编码（非本次验收阻塞项）。 |
| L2 | 信息 | `app/database.py:26` 的 `_connect_args` 判断 `settings.db_path.startswith("sqlite")`，对文件路径（如 `./data/insurance.db`）恒为 `False`，故 `check_same_thread=False` 实际未生效。属**既有代码**（非 v1.3 引入），实测服务运行正常。 | 建议后续统一为「方言 == sqlite」判断，非本次范围。 |

---

## 6. 智能路由判定

- **未发现源码 Bug**；发现的 2 处失败均为 **QA 测试代码自身问题**（缺填必填项导致校验前置命中），已由 QA **自行修复**。
- 判定：**`路由: NoOne`** —— v1.3 后端、迁移、分摊、多被保人写路径与前端交互**均通过独立验证**。
- 测试轮次：**Round 1 全绿**（详见 §7 Round 2 复验）。

---

## 7. Round 2 复验 — 枚举默认值去硬编码（L1 修复）

**背景**：工程师将 7 处前端硬编码枚举默认值改为经 `/api/meta/enums` 派生（新增 `format.js::enumDefault(list, index=0)`），涉及 `policyForm.js`(5 处) 与 `memberForm.js`(2 处)，新增 `DEFAULT_PAYMENT_TERM_INDEX=1` / `DEFAULT_GENDER_INDEX=2`。

### 7.1 代码审阅（Read 回读）

`enumDefault` 实现：`!Array.isArray(list) || list.length===0 → ''`；否则 `list[index] !== undefined ? list[index] : list[0]`。7 处调用均为 `data.X || enumDefault(enums.X[, index])` —— **编辑态优先取数据、新增态取派生默认值**。`static/js/components/` 下已无 `|| '枚举字面量'` 形式的默认值（Grep 复核，仅余 toast/弹窗文案类非枚举字符串）。

### 7.2 真实浏览器复验（无头 Chrome CDP）— **10/10 通过**

| # | 断言 | 结果 |
|---|---|---|
| R2-1 | 新增保单 5 下拉默认：险种=重疾险 / 状态=生效中 / 保障期限=终身 / 缴费期限=**固定年限**（非趸交）/ 缴费方式=月缴 | ✅ |
| R2-1b | 表单无 `"undefined"` 渲染 | ✅ |
| R2-2 | 新增成员：关系=本人 / 性别=**未知**（非男） | ✅ |
| R2-3 | 编辑保单（分红险）回填=分红险，缴费方式=年缴（**不被默认覆盖**） | ✅ |
| R2-4 | 编辑成员（钱七）回填=子女/女（**不被默认覆盖**） | ✅ |
| R2-5a | 新增保单保存成功（弹窗关闭） | ✅ |
| R2-5b | 提交的 `insuranceType` 确为下拉选中项（设为「年金险」→ 落库 年金险） | ✅ |
| R2-6a | 新增成员保存成功 | ✅ |
| R2-6b | 提交的 `relation/gender` 确为下拉选中项（子女/男 → 落库一致） | ✅ |
| R2-7 | `enumDefault` 退化：`undefined/null/[]→''`，越界 index→首项，`index=0/1` 正常，**全程无异常** | ✅ |

> 证据：`<临时目录>/qa_v13/r2_browser_out.txt` 与 `r2_browser_results.json`。

### 7.3 全量回归与静态检查

- `pytest tests/ -q` → **305 passed**（与改前一致）。
- `static/` 零外链三组 grep 全空；`format.js` 新增 `enumDefault` 导出正常。

### 7.4 Round 2 判定

**`路由: NoOne`** —— 去硬编码改动**未改坏任何表单**：7 处默认值与改前逐值一致、编辑回填不受影响、提交值来自真实下拉选中项、`enumDefault` 退化路径安全。

### 7.5 已知低风险项（已裁定，暂不处理）

**B 类「业务逻辑分支字面量」**（`coverageType==='终身'`、`paymentType==='趸交'`、`statusBadge`、`coverageTermText` 等）：属**条件规则**（与后端 `_validate_terms` / `calc_schedule` 同源），**非枚举选项列表副本**，且为 **v1.2 既有形态（非 v1.3 引入）**；消除需后端新增语义标志位 API，**范围外**。经 team-lead 裁定：**记为已知低风险项，不作为未解决缺陷**。
