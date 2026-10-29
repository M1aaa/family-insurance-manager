# 缴费记录（`paidPeriodsOverride`）+ 逾期增量 — QA 独立验证报告

| 项目 | 内容 |
|---|---|
| 验证人 | QA 工程师 严过关（software-qa-engineer） |
| 验证对象 | 缴费记录 + 逾期增量（`paidPeriodsOverride`） |
| 验证方式 | **独立重写参考实现交叉校验** + 端到端接口/迁移/无头浏览器验证 |
| 交付测试 | `tests/test_qa_payment_record.py`（19 项，未改动工程师的 `tests/test_payment_record.py`） |
| 最终判定 | **NoOne（全部通过，未发现源码缺陷）** |

> 真源：主理人给出的派生量语义定义 + 本报告自行独立重写的参考实现。
> `docs/ARCHITECTURE.md` §5.7 / `docs/PRD.md` 第 8 章仅作意图参考，未作为正确性证据。

---

## 1. 逐项结论表

| # | 验证项 | 结论 | 关键证据 |
|---|---|---|---|
| A1 | `due_date_for_period` 与 `compute_k_max` 同源 | ✅ 通过 | 3640 组断言，`due_date_for_period(eff,freq,n)==add_months(eff,(n-1)*pm)` 全等 |
| A2 | 派生量交叉校验（独立参考实现） | ✅ 通过（**0 不一致**） | 独立脚本 **1,204,320** 组合；套件内 **120,960** 组合，0 不一致 |
| A3 | `enumerate_upcoming` 三情形起点 + `guard<4` | ✅ 通过 | 三情形期次集合精确匹配；与「无 guard 暴力枚举」逐值一致，窗口内最多 3 期 |
| B4 | 向后兼容（`override IS NULL`） | ✅ 通过（**0 不一致**） | 25,200 组合与旧公式 `paid/next_due/is_paid_off/...` 逐值相同 |
| B5 | 既有测试全绿 | ✅ 通过 | `pytest -q` → **122 passed**（既有 103 + QA 19） |
| C6 | 存量旧库升级路径 | ✅ 通过 | 旧结构库启动后自动补列、数据行逐行不变、二次启动幂等 |
| C7 | 迁移失败不阻塞启动 | ✅ 通过 | 注入非法 DDL，`_run_additive_migrations` 不抛异常，合法列仍补上 |
| D8 | `pay-next` 契约与边界 | ✅ 通过 | 推进 / 逾期优先 / 已缴清 422 / 不存在 404 / `total=0` 422 |
| D9 | PUT `paidPeriodsOverride: null` 改回自动推算 | ✅ 通过（**回归已修复**） | 实测 override 70→null，`paidPeriods` 回落为 `expectedPeriodsToDate` |
| D10 | 校验边界 | ✅ 通过 | 负数/超 1200/趸交 2/固定年限超总期数 → 422；「交至某年龄」不误报且钳制生效 |
| D11 | 生效日在未来 | ✅ 通过 | `expectedPeriodsToDate==0`、`paidPeriods==0`、`overduePeriods==0` |
| D12 | 看板 `overdue` 清单 | ✅ 通过 | 已退保/已失效被排除；`earliestDueDate`/`amount` 正确；按 `(due, id)` 升序 |
| E13 | 无头浏览器 DOM 渲染 | ✅ 通过 | 看板/保单列表关键元素齐全，**无 JS 运行时错误** |
| E14 | 前端表单真实提交 | ✅ 通过 | 真实浏览器填表→`collect()`→POST→回读，`paidPeriodsOverride=77` 落库 |
| E15 | 零外部依赖 | ✅ 通过 | 两条 grep 均为空 |
| E16 | 前端 JS 语法 | ✅ 通过 | 11/11 文件 `node --check` OK |

---

## 2. 关键证据（命令 + 实际输出摘要）

### 2.1 磁盘实核对（针对 3 处历史缺陷 + 全量改动点）

不信任任何描述，直接读磁盘：

```
$ grep -n "policy.paid_periods_override = data.paid_periods_override" app/services/policy_service.py
213:    policy.paid_periods_override = data.paid_periods_override
$ grep -n "k = max(sched.paid_periods, sched.k_max + 1)" app/calc.py
312:    k = max(sched.paid_periods, sched.k_max + 1)
$ grep -n "paidPeriodsOverride," static/js/components/policyForm.js
370:        paidPeriodsOverride,
```

三处历史缺陷均已真实落盘。此外以 `grep -rnE "k_max|paid_periods|add_months|period_months" app/ --include=*.py` 扫描，
确认**派生量逻辑只存在于 `app/calc.py` 单一真源**，`policy_service` / `stats_service` / `member_service` 均复用
`calc_schedule`，无重复实现（无第 4 处「分叉公式」风险）。

### 2.2 A2 独立算法交叉校验（核心）

自行重写参考实现（`ref_add_months / ref_k_max / ref_schedule`，**不调用 `app.calc`**），
在「生效日含月末边界 1/29–1/31、2/29、3/31、4/30，月缴/年缴，today 落在缴费日当天/前一天/后一天，
趸交/固定年限/交至某年龄，override ∈ {NULL,0,1,k_max,k_max±1,total,total+N,1200}」上枚举：

```
checked_schedule_combos=1204320
kmax_mismatch=0
due_date_mismatch=0
mismatch_records=0
```

套件内保留规模适中的同源校验（可常规回归）：

```
tests/test_qa_payment_record.py::test_calc_schedule_matches_independent_reference PASSED   # 120,960 组合
tests/test_qa_payment_record.py::test_compute_k_max_matches_bruteforce PASSED               # 756 组暴力比对
tests/test_qa_payment_record.py::test_enumerate_upcoming_matches_bruteforce_and_guard_is_enough PASSED
tests/test_qa_payment_record.py::test_null_override_backward_compatible_with_old_formula PASSED
```

### 2.3 A3 三情形起点 + guard 兜底

| 情形 | k_max | expected | paid | overdue | upcoming 期次 | 结论 |
|---|---|---|---|---|---|---|
| 自动推算 | 62 | 63 | 63 | 0 | [64, 65] | ✅ |
| 提前缴(override=70) | 62 | 63 | 70 | 0 | [] | ✅ 已缴期次不泄漏 |
| 逾期(override=60) | 62 | 63 | 60 | 3 | [64, 65] | ✅ 逾期期次不入窗口 |

`enumerate_upcoming` 与「不受 `guard` 限制的暴力枚举」在 **~68,000** 组合上逐值一致；
60 天窗口内实测**最多 3 期**（月缴），故 `guard < 4` 足够。

### 2.4 B5 全量回归

```
$ .venv/Scripts/python.exe -m pytest -q
122 passed, 2 warnings in 21.30s
```

既有 103 项 + QA 新增 19 项全绿，零回归。

### 2.5 C 数据库迁移（重点覆盖）

`tests/test_qa_payment_record.py::test_migration_upgrades_old_db_and_is_idempotent`

- 造「旧结构」库：当前模型建表 → `ALTER TABLE policies DROP COLUMN paid_periods_override` → 写入真实数据行；
- 以 `DB_PATH` 环境变量让应用在该库上启动（子进程真实加载 `app`，执行 `create_all` + `_run_additive_migrations`）；
- 结果：`paid_periods_override` 被自动补上、`rows_after == rows_before`（逐行不变）；
- **二次启动幂等**：列集合不变、`paid_periods_override` 仅出现 1 次、数据不变。

`...::test_migration_fresh_db_created_with_column`：全新空库 `policies` 表此前不存在 → 由 `create_all` 直接建出含该列的表（不走 ALTER 分支）。

`...::test_migration_failure_does_not_block_startup`：注入非法 DDL（`INTEGER))`）触发 `ALTER` 语法错误，
`_run_additive_migrations` **不抛异常**，且同一轮中的合法列仍被补上 → 异常兜底真实有效。

### 2.6 D 接口契约与边界（实测）

`tests/test_qa_payment_record.py` 中 8 项接口测试全绿，含：

- `pay-next`：`paid=76 → 77`（overdue 3→2）、再 `77 → 78`（overdue 2→1，优先补缴最早一期）；不存在 → 404；
- `PUT paidPeriodsOverride: null`：实测 override 由 70 清空为 `None`，`paidPeriods` 回落为 `expectedPeriodsToDate`（历史缺陷端到端回归通过）；
- 校验：`-1 / 1201 / 趸交 2 / 固定年限 241` → 均 422；「交至某年龄」填 5 不误报 422 且被钳制为 `paid ≤ total`；
- 看板 `overdue`：仅「生效中/已缴清」；`earliestDueDate=2025-03-15`、`overduePeriods=3`、`amount=1500.0`（=3×500）；
- 端到端接线：接口返回派生字段与独立参考实现逐值一致（含趸交/年缴/月缴，NULL/0/1/3/7/999 各 override）。

### 2.7 E 前端独立验证（真实无头浏览器）

**关键说明**：为避免改动 `data/insurance.db`，我另起了一个**独立临时服务**（`DB_PATH` 指向临时库，
端口 `8123`，种子数据覆盖逾期/手动/待缴/已退保四种分支），验证后已停止并清理；`data/insurance.db` 与
`127.0.0.1:8000` 的服务**全程未被写入**（见 §4）。

```
$ chrome --headless=new --dump-dom "http://127.0.0.1:8123/#/dashboard"
总览看板=1  alert warn=1  检测到=1  逾期未缴=1  补缴一期=1  近 60 天待缴清单=1  >操作<=1
data-pay-policy=2（待缴「已缴」按钮 + 逾期「补缴一期」按钮）  有逾期，请先补缴=1

$ chrome --headless=new --dump-dom "http://127.0.0.1:8123/#/policies"
保单列表=2  缴费进度=1  已缴 76/240 期  已缴 6/10 期  已缴 72/360 期
badge manual=3（override=0 也正确显示「手动」）  badge warn=2（逾期 3 期 / 逾期 93 期）
```

- **JS 运行时错误**：`--enable-logging=stderr --v=1` 日志中无 `Uncaught / SyntaxError / TypeError / ReferenceError`；DOM 未停留在「加载中…」。
- **表单真实提交（E14，回归第 3 处缺陷）**：临时页面在**真实浏览器**中 `import { createPolicyForm }`，填表后调用 `collect()` 并 POST：

```
QA_RESULT {"collect_ok":true,"payload_type":"number","payload_value":77,
           "created_value":77,"fetched_value":77,"fetched_paidPeriods":77,
           "fetched_overduePeriods":2}
```

  即「已缴期数」被正确收集并**真的落库**（该页面为临时探针，验证后已删除）。
- **零外部依赖（E15）**：

```
$ grep -rEn "https?://" static/ | grep -v "www\.w3\.org/2000/svg"      # 空
$ grep -rEn '(src|href)\s*=\s*"https?:|url\(\s*https?:|@import' static/  # 空
```

- **JS 语法（E16）**：11/11 前端文件复制为 `.mjs` 后 `node --check` 全部 OK。
- CSS 类齐全：`.badge.manual / .badge.warn / .alert.warn / .progress-cell / .progress > span / .field-hint / .btn.small` 均已定义。

---

## 3. 发现的问题

### 3.1 源码缺陷：**无**

本次增量在算法、接口、迁移、前端四个层面均未发现源码缺陷。

> 说明：验证过程中我自己写的 1 个断言阈值有误（`test_compute_k_max_matches_bruteforce` 期望
> `checked>=2000`，实际矩阵为 756）——**属测试代码缺陷，已由 QA 自行修正**为 `>=700`，
> 并非源码问题，故路由判定仍为 `NoOne`。

### 3.2 已知边界 / 未覆盖风险（非本次缺陷，建议记录）

| 项 | 说明 | 影响 |
|---|---|---|
| `enumerate_upcoming` 的 `guard < 4` | 若 `window_days > 60`（如 365），窗口内期次会被截断到最多 4 期 | **当前无影响**：`stats_service.WINDOW_DAYS=60` 固定、`calc` 默认 60；已用用例显式记录该边界 |
| `app/__init__.py::__version__` | 仍为 `"1.0.0"`，而文档已标注 v1.2；健康检查返回 `1.0.0` | 仅元信息/文档不一致，无功能影响 |
| `data/insurance.db` 示例数据 | 两张示例保单 `effectiveDate` 均为今天 → `expectedPeriodsToDate=0`，无法在**线上 8000 实例**上演示逾期/待缴 UI | 验证时改用独立临时库覆盖（见 §2.7），非缺陷 |

---

## 4. 环境副作用声明

- **未改动 `data/insurance.db`**：验证前后逐行比对一致（`count=2`，`paid_periods_override` 均为 `NULL`，`updated_at` 未变，`members=2`）。
- **未重启 8000 端口服务**：验证全程 `http://127.0.0.1:8000/api/health` 均返回 200；所有写操作均针对独立临时库。
- 临时资源：在 `8123` 端口起过独立服务（`DB_PATH`→临时库），验证后已 `kill`；临时库/Chrome profile/探针脚本均已删除，`static/qa_probe_form.html` 已删除，8123 端口已释放。

---

## 5. 智能路由判定

> **判定：`NoOne`（全部通过）**

依据：

1. 独立参考实现在 **1,204,320** 组合（另套件内 12 万+）上与 `app.calc` **0 不一致**，含月末钳制、未来生效、趸交、逾期/提前缴全部边界；
2. 既有 103 项 + QA 新增 19 项 = **122 项全部通过**，零回归；
3. 迁移（旧库升级 / 幂等 / 全新库 / 失败兜底）、接口契约（含历史缺陷回归）、前端真实渲染与表单提交均通过；
4. 未发现任何源码 Bug，无需回退 Engineer。

交付物：

- `tests/test_qa_payment_record.py`（19 项独立测试，未修改工程师的测试文件）
- `docs/qa-report-payment-record.md`（本报告）
