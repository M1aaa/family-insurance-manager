# QA 终验报告 —— v1.2 容器化交付物 · 阶段 B（独立验证 + 构建模拟）

> 版本：v1.2 · 阶段 B 终验
> 验证人：QA 工程师（Edward）
> 代码状态：**已冻结**（工程师收尾轮已完成）
> 判定：**NoOne（全部通过，无源码缺陷，可交付）**
> 本机 Docker：**未安装**（`docker`/`podman`/`nerdctl`/`buildah`/`docker-compose` 均 NOT FOUND，`wsl.exe` 禁用）→ 容器化交付物以「静态审查 + 等价构建模拟」验证，**未做真机构建/运行**。

---

## 一、结论速览

| 项 | 内容 | 结果 |
|---|---|---|
| 阶段 A 遗留缺陷 D1~D4 | 工程师已修，QA 独立复核 | ✅ 4/4 已修复 |
| README API 表 vs 真实路由 | 19 条逐条比对 | ✅ 19/19 一致，差异 0 |
| overview.md 数字与事实 | 与磁盘/实测核对 | ✅ 全部一致 |
| **B1** 等价构建模拟 | 仓库外全新 venv 仅装 requirements.txt → 起服务 → 真实 HTTP 走通 v1.2 全功能 | ✅ 全绿 |
| **B2** 旧库冷启动升级 | v1.1 结构库 → 启动即补 7 列 + 建附件表，旧数据不变 | ✅ 通过 |
| **B3** 前端真实浏览器 E2E | Puppeteer 驱动 Chrome，33 项独立断言 | ✅ 33/33 |
| **B4** 零外部依赖复核 | 扫描 static/ 全部文件 | ✅ 通过 |
| **B5** 真实数据未污染 | `data/insurance.db` md5 前后一致 | ✅ 未变 |
| 后端全量测试 | 独立复跑 `pytest -q` | ✅ **214 passed** |

**总判定：NoOne —— 无源码缺陷，容器化交付物与 v1.2 全功能经独立验证通过。**

---

## 二、方法与边界

- **本机无 Docker**：无法执行 `docker build` / `docker compose up`。故采用**等价构建模拟**（B1）替代：镜像的实质内容 = `requirements.txt` + `app/` + `static/`（见 `Dockerfile` 的 `COPY`），在**仓库之外**的干净目录中复现这三者，用**全新 venv** 仅装 `requirements.txt`，起真实 uvicorn 并跑真实 HTTP。
- **不改业务代码**：全程只读业务代码；仅新增测试脚本与报告，且临时脚本统一置于 `docs/qa/`（该目录被 `.dockerignore` 排除，不进镜像）。
- **真实数据保护**：所有验证使用**临时数据库 + 临时附件目录**；真实 `data/insurance.db` 全程未触碰（B5 复核 md5）。

---

## 三、阶段 A 遗留缺陷 D1~D4 独立复核

| 编号 | 缺陷（阶段 A 提出） | 修复 | QA 复核证据 | 结论 |
|---|---|---|---|---|
| **D1** | `.dockerignore` 未排除 `_verify/` 等协作临时产物（构建上下文达 36.9 MiB） | 已补充 `_verify/`、`qa_*.py`、`qa_*.txt`、`_*.py`、`_*.txt`、`_*.mjs`、`_*.log`、`_*.png`、`_*.json`、`_*.pdf`、`_render_test.mjs` | 读回 `.dockerignore`（第 42–55 行）确认全部规则到位；仓库根目录复核**只剩交付物**（无 `_verify/`、无散落临时文件） | ✅ 已修 |
| **D2** | `scripts/*.sh` 无可执行位；README/DEPLOY 未提示 `chmod +x` | 已补可执行位 + 文档提示 | `ls -la scripts/` → `build-image.sh` 权限 `-rwxr-xr-x`；README 第 91 行、`docs/DEPLOY-ZSPACE.md` 第 73 行均含 `chmod +x scripts/build-image.sh` | ✅ 已修 |
| **D3** | README「API 概览」方法/路径有笔误（缺 `pay-next`、附件删除/content 等） | 已整表重写 | 见下节「README API 表逐条比对」——**19/19 一致，差异 0** | ✅ 已修 |
| **D4** | `overview.md` 数字与事实超前/不符 | 已重写 | 见下节「overview.md 核对」——数字与磁盘/实测**全部一致** | ✅ 已修 |

### 3.1 README API 表 vs 真实路由（逐条比对）

方法：用 `app.openapi()` 枚举权威路由（`app.routes` 在 FastAPI 0.141 中表现为 `_IncludedRouter` 包装，故以 OpenAPI schema 为准），再解析 `README.md` 第四节表格，**规范化路径参数后逐条比对**。

- **真实路由总数：19**（全部在 `/api` 前缀下）
- **README 声明：19**
- **缺失（真实有、README 无）：0**
- **多余（README 有、真实无）：0**

19 条全部一致：`health`、`meta/enums`、`members`(GET/POST)、`members/{id}`(PUT/DELETE)、`policies`(GET/POST)、`policies/{id}`(GET/PUT/DELETE)、`policies/{id}/pay-next`(POST)、`policies/batch-update`(POST)、`policies/suggestions`(GET)、`policies/{id}/attachments`(POST/GET)、`policies/{id}/attachments/{attId}`(DELETE)、`policies/{id}/attachments/{attId}/content`(GET)、`stats/dashboard`(GET)。

> 说明：框架内置的 `/docs`、`/redoc`、`/openapi.json` 不在 README 表格内，但 README 正文已说明「交互式接口文档 `/docs`」，非缺陷。

### 3.2 overview.md 数字与事实核对

| overview 声明 | 独立核对结果 |
|---|---|
| 后端全量测试 **214 passed** | ✅ 独立复跑 `pytest -q` → `214 passed, 2 warnings in 41.33s` |
| 前端语法检查 **14 / 14** 个 JS 文件 | ✅ `find static -name '*.js'` = **14**（顶层 5 + components 6 + views 3） |
| 前端真实浏览器验收 **39 / 39** | ✅ 由本报告 B3 以**独立断言**（33 项）另行覆盖，结论一致（均通过） |
| **7 张截图** | ✅ `docs/screenshots/` = 7 张 PNG |
| 存在 **`docs/qa/`** | ✅ 存在（本报告证据目录） |
| 「本机未安装 Docker」 | ✅ 复核确认无任何容器运行时 |

---

## 四、B1 —— 等价构建模拟（真实 HTTP 端到端）

**模拟设置**（仓库外的独立临时目录）：
- 仅放入 `requirements.txt` + `app/` + `static/`（等价于 Dockerfile 的 `COPY`；已剔除 `__pycache__`，与 `.dockerignore` 一致）。
- **全新 venv** → `pip install --no-cache-dir -r requirements.txt` → **`pip_exit=0`**，**无缺失依赖**（解析出 fastapi/uvicorn/sqlalchemy/pydantic/openapi/starlette 等 24 个包，全部来自 `requirements.txt`）。
- 以该 venv 起 `uvicorn app.main:app --port 8011`（真实进程），用临时 v1.1 结构库。

**真实 HTTP 结果（全部通过）**：

| 检查 | 结果 |
|---|---|
| `GET /api/health` | `{status:"ok", db:"ok", version:"1.2.0"}` |
| `GET /api/meta/enums` | 7 组枚举齐全 |
| 建成员 / 建保单 | 201 |
| **售后 7 字段落库** | advisorName/Wechat/Email/companyPhone/channelPhone 均正确回读 |
| **批量三态** | 设值 ✅ / 显式清空(null) ✅ / 留空不改 ✅ |
| `GET /api/policies/suggestions` | 6 字段，`advisorName` 聚合计数正确 |
| **附件上传** | 201，`attachmentCount=1` |
| 附件列表 | 200，中文名 `电子保单_产品A.pdf` |
| **附件 content** | 200，21 字节，`Content-Disposition: attachment; filename*=utf-8''%E7%94%B5...`（RFC 5987） |
| 非 PDF 拒绝 | 422 `INVALID_FILE_TYPE` |
| 删除附件 / 删保单 | 200 |
| `GET /api/stats/dashboard` | 200，6 组数据 |
| 静态前端 | `GET /` 200 含 `<html>`；`GET /js/main.js` 200 |
| 旧保单可读 | 200 |

**结论：B1 全绿** —— 镜像实质内容在「干净环境 + 仅运行依赖」下可正常启动并走通 v1.2 全部功能。

---

## 五、B2 —— 旧库冷启动升级（真实 uvicorn）

在 B1 中同时验证：预置一个**真实 v1.1 结构**的 SQLite 库（`policies` 表**无** 7 个售后列、**无** `policy_attachments` 表），启动服务后：

- `policies` 表**已补齐 7 列**：`advisor_name / advisor_phone / advisor_wechat / advisor_email / company_phone / channel_name / channel_phone` ✅
- **已创建** `policy_attachments` 表 ✅
- **旧数据一字未改**：`p_old` 行仍在且 `company='平安'` ✅
- 旧保单经 API 可正常读取 ✅

**结论：增量迁移幂等生效，升级既有库无需手工 ALTER。**

---

## 六、B3 —— 前端真实浏览器 E2E（独立断言 33/33）

**工具链装在仓库之外**（独立工具目录，本仓库不产生 `node_modules`/`package.json`）。
浏览器：Puppeteer 驱动 `chrome-headless-shell 153.0.8010.36`，启动参数含 `--no-proxy-server`（规避本机代理导致的 `ERR_ALPN_NEGOTIATION_FAILED`）。
服务：模拟 venv 起 uvicorn（临时库，端口 8012）。上传用纯 ASCII 路径 `sample_e2e.pdf`。

**断言结果：33 / 33 通过，0 失败；无 pageerror、无 console.error、无真实请求失败。**

覆盖（对应 team-lead 指定重点 ①~⑦）：

| # | 重点 | 断言要点 | 结果 |
|---|---|---|---|
| ① | **三态（浏览器侧）** | 勾选 2 张 → 仅填「服务顾问·姓名」→ 保存后两张均改、未填字段（advisorPhone/channelName）保持各自原值；开启「高级」→ 勾选 advisorPhone「设为空」→ 落库为 `null` 且未波及 advisorName；未填任何字段 → 阻止并提示「未填写任何要修改的字段」 | ✅ |
| ② | **`collect()` 7 字段落库** | 编辑弹窗填入 7 个售后字段 → 保存 → API 回读 7 字段逐一相等 | ✅ |
| ③ | **三入口常用值提示** | 编辑弹窗 / 列表「+新增保单」/ 顶栏「+新增保单」三入口的 `<datalist>` 均含历史值「王顾问」（另含「E2E统一顾问」） | ✅ |
| ④ | **勾选状态保持** | 勾选 1 张 → 触发筛选重渲染 → 勾选态与「批量编辑 (1)」计数均保持 | ✅ |
| ⑤ | **附件全流程** | 初始「暂无附件」→ 上传后计数「已挂载 1 个附件」+ 文件名展示 → 「查看」调用 `window.open(url,'_blank','noopener')` 指向 `?disposition=inline` → 「下载」触发 `<a>` 指向 `?disposition=attachment` → 服务端 content 分别返回 `inline` / `attachment` 头且 `Content-Type: application/pdf` → 删除出现内联二次确认 → 删除后计数归 0 | ✅ |
| ⑥ | **无 JS 异常** | 全程 `pageerror` / `console.error` / 真实请求失败均为空 | ✅ |
| ⑦ | **仪表盘 / 成员回归** | `#/dashboard`、`#/members` 均渲染且可见 | ✅ |

> **测试脚本自身缺陷（已自修，非源码问题）**：① 首轮 `page.type` 未先清空预填值导致追加；② 选择器 `.after-sales-grid .suggestion-datalists` 写错（该 `<div>` 实为 `.after-sales-grid` 的**兄弟节点**）；③ headless-shell 下「新标签检测」不稳 → 改为拦截 `window.open` 断言入参；④ 真实下载触发的 `net::ERR_ABORTED` 属浏览器正常中止，已单列不计负。上述均为 QA 脚本问题，**未回退工程师**。

---

## 七、B4 —— 零外部依赖复核

扫描 `static/` 下 **17 个文件**：

- 外部链接（`http(s)://`，排除 `127.0.0.1`/`localhost`/SVG 命名空间等无害引用）：**0**
- 协议相对外链（`//cdn...`）：**0**
- `import ... from "http..."` / `importScripts("http...")`：**0**
- `index.html` 引用的本地资源（`/favicon.svg`、`/css/styles.css`、`/js/main.js`）**全部存在**：**0 缺失**

**结论：前端零外部依赖、无构建步骤，`PASS=true`。**

---

## 八、B5 —— 真实数据未污染

| 时点 | `data/insurance.db` md5 | 大小 | mtime |
|---|---|---|---|
| 验证前 | `bdf59b1d0da092a2553ccb8f99940e4a` | 36864 | 13:40 |
| 验证后（B1/B2/B3/全量测试跑完） | `bdf59b1d0da092a2553ccb8f99940e4a` | 36864 | 13:40 |

- md5、大小、mtime **完全一致**；未生成 `data/attachments/`。
- 仓库根目录复核**只剩交付物**，无临时文件残留。

**结论：真实数据与仓库工作区均未被污染。**

---

## 九、已知限制（诚实列出，不构成缺陷）

1. **本机无 Docker，未做真机构建/运行**：`Dockerfile` / `docker-compose*.yml` 仅经静态审查 + 等价构建模拟（B1/B2）。建议在有 Docker 的机器上补跑 `docker compose -f docker-compose.build.yml up -d --build` 做最终确认。
2. **`linux/amd64` 平台依赖解析未在真机核对**：B1 的依赖完整性模拟在 `win_amd64` 上完成；镜像目标为 `linux/amd64`，该平台下实际 pip 解析未在真机验证。
3. **极空间固件「相对路径卷」行为未真机验证**：`docker-compose.yml` 用 `./data:/app/data`，不同 NAS 固件支持度需实机确认；指南已给出绝对路径备选。
4. **`docs/qa/` 下的 QA 脚本为过程性证据**：其中早期由协作流程迁移至此的 `qa_v12_http_check.py` 的相对路径基准（`Path(__file__).parent`）在移动后已不再指向仓库根，属 QA 自用脚本、非交付物；`docs/` 被 `.dockerignore` 排除，**不进镜像**，不影响交付。
5. **本机 shell 的 stdout 捕获与 PATH 异常**（`dirname: command not found`）：属本机 QA 环境怪癖，所有命令改为重定向到文件后读回，不影响结论。

---

## 十、证据索引（`docs/qa/`）

| 文件 | 内容 |
|---|---|
| `qa_routes_crosscheck.py` / `_qa_routes_out.json` | README API 表 vs 真实路由比对（19/19） |
| `qa_b1_build_sim.py` / `_qa_b1_out.json` | B1 构建模拟 + B2 旧库升级（真实 HTTP） |
| `_qa_sim_venv.log` / `_qa_sim_pip.log` | 全新 venv 与 `pip install` 记录（`pip_exit=0`） |
| `qa_b3_frontend_e2e.cjs` / `_qa_b3_out.json` | B3 前端浏览器 E2E（33/33） |
| `qa_b4_zero_dep.py` / `_qa_b4_out.json` | B4 零外部依赖复核（PASS） |
| `_qa_pytest_full.txt` | 全量 `pytest -q`（214 passed） |
| `_qa_realdb_before.txt` / `_qa_realdb_after.txt` / `_qa_realdb_final.txt` | B5 真实库 md5 前后一致 |
| `qa_docker_audit.py` / `qa_docker_deps_check.py` / `_qa_docker_*.txt` | 阶段 A 静态审计证据 |

---

## 十一、最终判定

> **NoOne** —— 阶段 A 的 4 处缺陷已全部修复并经独立复核；B1~B5 全项通过；README / overview 与真实代码/磁盘事实一致；前端零依赖、无 JS 异常；真实数据未受污染。
> **v1.2 容器化交付物可交付**（唯一保留项：建议在有 Docker 的环境补跑一次真机构建，属环境限制而非交付缺陷）。
