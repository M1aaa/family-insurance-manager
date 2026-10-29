# QA 独立验证报告 · v1.2 后端（售后信息 · 批量编辑 · 电子保单 PDF）

> 验证人：严过关（QA Engineer） · 范围：**仅后端**（前端由工程师并行开发，不在本次范围）
> 基线：工程师自报 `pytest -q` → 163 passed。本次为**独立证伪**，结论全部来自 QA 新写的测试与脚本。

---

## 0. 结论摘要

| 项 | 结论 |
|---|---|
| **智能路由判定** | **NoOne**（全通过，未发现源码 Bug） |
| 全量 `pytest -q` | **214 passed**（工程师 163 + QA 新增 **51**） |
| 6 个高风险点 | **全部实测通过** |
| 发现的源码 Bug | **0** |
| 用户真实数据 | `data/insurance.db` **未被污染**（md5 前后一致） |

---

## 1. 验证范围与方法

**方法（三条独立证据链）**：

1. **独立单元/集成测试**（不复用工程师断言，不 import 其测试文件）：
   - `tests/test_qa_v12_backend.py`（19 用例）：批量编辑三态随机交叉比对、校验边界、常用值聚合、向后兼容、N+1。
   - `tests/test_qa_v12_attachments.py`（26 用例）：路径穿越、magic bytes、流式限流、all-or-nothing、删除顺序、CASCADE、Content-Disposition、数量上限。
   - `tests/test_qa_v12_migrations.py`（6 用例）：真实 v1.1 库升级、幂等、失败隔离、新表 DDL。
2. **真实 HTTP 端到端**：`docs/qa/qa_v12_http_check.py` 以临时 v1.1 库启动 `uvicorn app.main:app --port 8010`，走完整业务流。
3. **真实 SQLite 行为**：迁移与 CASCADE 均对真实磁盘库校验，非仅读源码。

**隔离**：所有文件操作经 `tmp_path` 覆盖 `settings.attachments_dir`；数据库用内存/临时库。**全程未触碰 `data/`**。

---

## 2. 高风险点实测结果

### ★★★ 2.1 批量编辑「三态语义」

**独立参考实现**：QA 自写 `_ref_apply()`（不复用 `policy_service`），对「键缺失 / 字符串 / 全空格 / `null`」分别建模，再与真实 API 的落库结果**逐字段**比对。

| 验证项 | 结果 |
|---|---|
| 随机交叉比对 | **2000 组请求 × 139,790 个字段单元 → 差异 0** |
| 初始值含 `NULL` 与多种字符串（中文/数字/邮箱/首尾空格） | ✅ |
| `values` 混合「不发键 / 字符串 / 全空格 / `null`」 | ✅ 全部符合参考实现 |
| **只填顾问姓名** → 其余 6 字段**逐保单**保持原值（含 NULL 保单） | ✅ |
| `ids=[]` → 422 `POLICY_IDS_EMPTY` | ✅ |
| `values={}` → 422 `FIELD_VALUES_EMPTY` | ✅ |
| 白名单外键（`company`/`productName`/`id`/`policyholderId`/`paidPeriodsOverride`）→ 422 `UNKNOWN_UPDATE_FIELD` | ✅ |
| 超长值（每字段 limit+1）→ 422 `VALIDATION_ERROR`；恰好到界（len==limit）→ 200 | ✅ 7 字段全覆盖 |
| 不存在 id 忽略并计入 `missingIds`；`requested == updated + len(missingIds)` | ✅ |
| 全部 id 不存在 → 200 / updated=0 / 不报错 | ✅ |
| 重复 id 去重（requested 为去重后数量） | ✅ |
| 幂等（同请求连发两次，响应一致） | ✅ |
| 白名单外列零改动（`company`/`productName`/`status` 不变） | ✅ |

> **观察项（非 Bug，已登记）**：长度校验按**未 trim 的原始字符串**判定，故 `" " * 21`（21 空格）对 `advisorName`（≤20）返回 422。前端提交前已 trim，不影响验收。

### ★★★ 2.2 附件安全

| 验证项 | 结果 |
|---|---|
| **路径穿越**：`../../evil.pdf`、`..\..\evil.pdf`、`/etc/passwd`、`C:\Windows\evil.pdf`、`....//....//evil.pdf`、`sub/dir/evil.pdf` | ✅ 落盘名**全部**为 `^[0-9a-f]{32}\.pdf$`；扫描**两层父目录**新增文件**全部**位于 `attachments_dir` 内，**0 个逃逸** |
| 控制字符文件名（`\r\n`、`\x00`、纯控制字符） | ✅ 无 500、无逃逸；入库 `fileName` 不含任何控制字符 |
| magic bytes：`.txt` 改名 `.pdf`（内容非 PDF）→ 422 `INVALID_FILE_TYPE` | ✅ 目录 0 文件 |
| magic bytes：真 PDF 改名 `.txt` → **以文件头为准**，成功 201 | ✅ **与设计一致** |
| 空文件 → 422 | ✅ |
| 流式限流：上限 1KB + 5KB 文件 → **413**，目录 **0 文件**（无半截文件） | ✅ |
| all-or-nothing（第 2 个非法）→ 第 1 个也回滚；DB **0 行**；目录干净 | ✅ |
| all-or-nothing（第 2 个超大）→ 同上 | ✅ |
| 删除顺序：`os.remove` 失败时仍返回 **200**，且 **DB 行已删** | ✅ 证「先提交 DB 再 best-effort 删文件」 |
| 手工删磁盘文件后请求 `content` → 404 `ATTACHMENT_NOT_FOUND` | ✅ |
| `Content-Disposition`：中文名输出 RFC 5987 `filename*=utf-8''...`，**无 `\r\n`** | ✅ |
| 数量上限：默认 10 → **恰好 10 通过、第 11 个 409** 且**不写任何文件** | ✅ |
| 批量上传致总数超限 → 409，**一个文件都不写** | ✅ |
| **CASCADE**：删保单 → `deletedAttachments=2`、`policy_attachments` 残留 **0**、磁盘文件清理 | ✅ |
| 同名文件上传两次 → 2 行、2 个不同 `stored_name`、2 个文件 | ✅ |
| 超长文件名（300+ 字符）→ 截断 ≤255，上传成功 | ✅ |
| Unicode 文件名往返（入库 = 列表 = 原值） | ✅ |
| 越权：附件不属于该保单 → 404；列表/上传不存在保单 → 404 | ✅ |

### ★★ 2.3 增量迁移（旧库升级）

| 验证项 | 结果 |
|---|---|
| 手工构造**真实 v1.1 结构**库（`members` 2 行 + `policies` 3 行全字段，无 7 列、无 `policy_attachments`） | ✅ |
| 升级后 7 列补齐、`policy_attachments` 建好 | ✅ |
| **原有数据逐行一字不改**（对原始 19 列做逐行元组比对，非仅行数） | ✅ |
| 旧行 7 新列均为 `NULL` | ✅ |
| 幂等：连跑两次不报错、列集合一致、数据不变 | ✅ |
| **失败不阻塞**：inspector 谎报列缺失 → `ALTER TABLE` 全部失败 → **不抛异常**、列未被破坏 | ✅ |
| 新表 DDL：`uq_attachments_stored` 唯一约束 / `idx_attachments_policy` 索引 / `ON DELETE CASCADE` | ✅ |
| FK CASCADE 真实生效（`PRAGMA foreign_keys=ON` 下删保单 → 附件行归零） | ✅ |
| **真实启动期迁移**：uvicorn 冷启动加载 v1.1 库 → 7 列 + 新表自动补齐 | ✅ |

### ★★ 2.4 常用值聚合 `GET /api/policies/suggestions`

| 验证项 | 结果 |
|---|---|
| 去重、`count DESC, value ASC`（同次数按值升序） | ✅ |
| 空历史 → 6 键均为空数组 | ✅ |
| `limit` 默认 20、范围 1~50；`limit=0`/`limit=51` → 422；`limit=1`/`50` → 200 | ✅ |
| **`companyPhone` 不出现**在结果里（含其历史值字符串） | ✅ |
| **路由未被 `{policy_id}` 吞掉**：返回 6 键聚合结构（非 404 / 非保单对象） | ✅ |
| 仅统计非空且 trim 后非空（`NULL` / `""` / `"   "` 均排除） | ✅ |

### ★★ 2.5 向后兼容

| 验证项 | 结果 |
|---|---|
| 旧保单（7 售后字段全 `NULL`）列表 / 详情 / 更新 / 删除照常 | ✅ |
| `PolicyOut` 只多 7 字段 + `attachmentCount`，**不含附件明细数组** | ✅ |
| `/api/health` → `version == "1.2.0"` | ✅ |
| `/api/stats/dashboard` 照常 200 | ✅ |

### ★★ 2.6 性能 / N+1

| 验证项 | 结果 |
|---|---|
| 200 张保单 + 若干附件，请求列表 | ✅ |
| 附件计数 SQL 条数 | **1 条**（且含 `GROUP BY`），**非** 200 次逐张查询 |
| 列表接口总 SQL 条数 | **≤ 8**（常数级） |
| 逐张核对 `attachmentCount` 正确性 | ✅ 全部命中期望值 |

---

## 3. 真实 HTTP 验证摘要（uvicorn · port 8010）

以临时 v1.1 库启动真实服务，`docs/qa/qa_v12_http_check.py` 输出（节选）：

```json
{
  "health": { "status": "ok", "db": "ok", "version": "1.2.0" },
  "migration": {
    "after_sales_columns_added": true,
    "attachment_table_created": true,
    "legacy_row_intact": true
  },
  "batch_update": { "status": 200,
    "body": { "requested": 2, "updated": 2, "updatedIds": ["p_...", "p_..."], "missingIds": [] } },
  "batch_three_state": { "advisorName_set": true, "advisorPhone_cleared": true, "channelName_untouched": true },
  "upload": { "status": 201, "attachmentCount": 1 },
  "list": { "status": 200, "count": 1, "name": "电子保单_产品A.pdf" },
  "content": { "status": 200, "bytes": 21,
    "content_disposition": "attachment; filename*=utf-8''%E7%94%B5%E5%AD%90%E4%BF%9D%E5%8D%95_%E4%BA%A7%E5%93%81A.pdf" },
  "reject_non_pdf": { "status": 422, "code": "INVALID_FILE_TYPE" },
  "delete_attachment": { "status": 200, "body": { "deleted": true, "id": "a_..." } },
  "delete_policy": { "status": 200, "body": { "deleted": true, "id": "p_...", "deletedAttachments": 0 } },
  "suggestions": { "status": 200,
    "keys": ["advisorEmail","advisorName","advisorPhone","advisorWechat","channelName","channelPhone"],
    "advisorName": [{ "value": "新顾问", "count": 1 }] },
  "legacy_policy_readable": { "status": 200, "advisorName": null }
}
```

**要点**：启动期迁移在**真实进程**中生效；批量三态、上传/列表/下载/删除、级联删除、聚合接口全部返回符合契约；中文名 `Content-Disposition` 正确输出 RFC 5987。

---

## 4. 新写测试的 `pytest` 输出

```
tests/test_qa_v12_backend.py ........... (19)
tests/test_qa_v12_attachments.py ......... (26)
tests/test_qa_v12_migrations.py ...... (6)
51 passed, 2 warnings in 21.61s
```

交叉比对统计（`-s`）：

```
[QA] 随机交叉比对：2000 组请求，139790 个字段单元，差异 0
```

全量（工程师 + QA）：

```
214 passed, 2 warnings in 46.74s
```

---

## 5. 智能路由判定

**判定：NoOne（全通过）**

**依据**：
- QA 新写的 51 个用例**全部通过**，无源码 Bug 被触发；
- 2000 组三态随机交叉比对**差异 0**；
- 真实 HTTP 端到端与真实 SQLite 迁移行为均符合 PRD 第 9 章 / ARCHITECTURE 第 6 章契约；
- 已裁定接受的 5 项行为（B-14 全空格计数、`delete_attachments_for_policy` 只读、`content_type` 记录客户端值、`count_by_policy` 签名、DELETE 响应新增 `deletedAttachments`）经实测与裁定一致，**未作为 Bug 上报**。

**过程中发现的问题均归属 QA（测试代码自身缺陷），已自修，未回退 Engineer**：
1. `setattr` 误传 `InstrumentedAttribute`（应传字符串属性名）→ 修正；
2. 断言保单状态误写 `"在保"`（实际枚举值 `"生效中"`）→ 修正；
3. 迁移比对用 `SELECT *`，未剔除新增列导致误报 → 改为显式列比对。

> ⚠️ 印证本机陷阱：上述第 2 处修正**首次编辑后未落盘**（编辑工具报成功但磁盘未变），经 `grep` 读回发现并二次修正。所有测试文件改动均经**读回磁盘**确认。

---

## 6. 遗留问题与未覆盖项（诚实列出）

**未覆盖（明确不在本次后端验证范围或无法在本机可靠复现）**：
- **前端**（`static/js/*`）：由工程师并行开发，本次**未验证**。
- **P2 自动识别预填（R-38）**：设计已延后、未实现——**符合预期**，非缺陷。
- **并发 / 多进程 SQLite 写入**、**Docker 卷持久化实测**：分别属并发与容器化任务范围，本次未做。
- **真实 >20MB PDF 传输**：以配置缩小上限等价验证 413 路径，未做真实大文件压测。
- **浏览器内联预览**：属前端行为。

**观察项（非 Bug，供知悉）**：
- 批量编辑长度校验按**未 trim 原始长度**判定，纯空格超长会 422。
- `content_type` 记录客户端传值（不强制 `application/pdf`）——已裁定。
- 批量 `values` 全为纯空格时 `updated` 仍等于存在 id 数——已裁定（B-14 前端拦截）。

**已知的测试侧代价**：`test_attachment_count_uses_single_group_by_not_n_plus_1` 造 200 张保单，运行约 10s；三态交叉比对约 14s。

---

## 7. 证据与产物

| 类型 | 路径 |
|---|---|
| QA 测试（批量编辑 / 聚合 / 兼容 / N+1） | `tests/test_qa_v12_backend.py`（19 用例） |
| QA 测试（附件安全 / 生命周期） | `tests/test_qa_v12_attachments.py`（26 用例） |
| QA 测试（增量迁移） | `tests/test_qa_v12_migrations.py`（6 用例） |
| 真实 HTTP 端到端脚本 | `docs/qa/qa_v12_http_check.py` |

**用户真实数据未被污染的证据**：
- 验证前：`data/insurance.db` md5 `bdf59b1d0da092a2553ccb8f99940e4a`，size `36864`，1 成员 / 2 保单；
- 验证后：md5 `bdf59b1d0da092a2553ccb8f99940e4a`，size `36864`，1 成员 / 2 保单 —— **完全一致**；
- 全程未生成 `data/attachments/`（附件测试一律落 `tmp_path`）。
