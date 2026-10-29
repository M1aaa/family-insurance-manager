# QA 独立验证报告 · Docker 交付物（阶段 A：静态审计 + 依赖完整性）

> 验证人：严过关（QA Engineer） · 任务 #15 · **阶段 A**（不依赖前端）
> ⚠️ **本机无任何容器工具**：`docker` / `podman` / `nerdctl` / `buildah` / `docker-compose` 全部 NOT FOUND，`wsl.exe` 被安全策略禁用。
> **因此本报告不含任何真机构建 / 运行结果；「构建模拟」一律为等价逼近，已在每处标注。**

---

## 0. 结论摘要

| 项 | 结论 |
|---|---|
| **智能路由判定** | **Engineer**（4 处交付物缺陷，均非阻断级；详见 §4） |
| 静态审计项 | Dockerfile / 2×compose / .dockerignore / 2×构建脚本 / 3×文档 / 零依赖 / 真实数据保护 —— **共 60+ 断言，5 项 FAIL（4 缺陷 + 1 误报已澄清）** |
| A2 依赖完整性 | **PASS**（全新 venv + 仅 requirements.txt + `--only-binary=:all:` 全绿，6 接口 200） |
| A3 零外部依赖 | **PASS**（`static/` 无任何 CDN / 外链 / 构建产物） |
| A4 真实数据不进镜像 | **PASS**（无 `COPY data`；真实库 md5 未变） |
| 真机构建 | **未做**（本机无 Docker） |

---

## 1. 验证范围与方法

**方法**：全部以「**实际执行的命令 / 断言 + 实测输出**」取证，不依赖「文件存在」。
- `docs/qa/qa_docker_audit.py`（只读）→ 输出 `docs/qa/_qa_docker_audit.txt`：Dockerfile / compose / .dockerignore / 脚本 / 文档 / 零依赖 / 真实数据 / **HEALTHCHECK 真实探活**。
- `docs/qa/qa_docker_deps_check.py` → 输出 `docs/qa/_qa_docker_deps.txt`：**全新 venv** 只装 `requirements.txt` → 起服务 → 逐个打接口。
- 全部结论可在上述两个证据文件复核。

> 环境说明：本机 shell 的 PATH 损坏（`dirname: command not found`），故所有命令输出**重定向到文件后读回**取证。

---

## 2. 逐项审计结论（含实际命令/断言）

### A1-1 Dockerfile —— 20/20 断言 PASS

实际断言（节选，完整见 `docs/qa/_qa_docker_audit.txt` A1-1）：

```
[PASS] ARG BASE_IMAGE=python:3.13-slim      [PASS] FROM ${BASE_IMAGE}
[PASS] ARG PIP_INDEX_URL 有默认             [PASS] ARG TZ 有默认
[PASS] ENV DB_PATH=/app/data/insurance.db   [PASS] ENV PORT=8000   [PASS] ENV TZ=${TZ}
[PASS] WORKDIR /app
[PASS] 安装 tzdata                          [PASS] 软链 /etc/localtime
[PASS] COPY requirements.txt 先于代码（依赖层缓存）
[PASS] COPY app ./app   [PASS] COPY static ./static   [PASS] mkdir -p /app/data
[PASS] LABEL version=1.2.0   [PASS] EXPOSE 8000
[PASS] CMD 为 exec 形式(JSON数组)   [PASS] CMD 内含 exec 前缀
[PASS] HEALTHCHECK python 片段语法可编译
```

**`tzdata` 结论**：`python:3.13-slim` 确实不含 tzdata（Dockerfile 注释属实）；`apt-get install tzdata` + `ln -snf /usr/share/zoneinfo/${TZ} /etc/localtime` + `echo ${TZ} > /etc/timezone` 是 Debian 系让 `TZ` 生效的标准做法，**逻辑正确**（真机效果需 Linux 验证，本机无法验证）。

**`HEALTHCHECK` 真实执行**（A1-1b，**实起服务**）：

```
服务就绪：True
[PASS] 对存活服务探活 → exit=0（期望 0）
[PASS] 对空端口探活   → exit=1（期望非 0）
```

即用本机 Python 原样执行 Dockerfile 里那条 `python -c "..."`，对 `127.0.0.1:8011/api/health`（200）返回 0、对空端口返回 1，**语法与逻辑均正确**。

**`CMD` / SIGTERM**：`CMD ["sh","-c","exec uvicorn ..."]` 为 exec 形式，`exec` 使 uvicorn 取代 sh 成为 PID 1，**SIGTERM 可正确传递**（静态正确）。**真机优雅停止未能验证**（Windows 无 SIGTERM 语义 + 无 Docker）。

### A1-2 `docker-compose.yml`（极空间运行用）—— 11/11 PASS

实际用 `pyyaml` 解析后断言：

```
[PASS] image == family-insurance-manager:1.2.0     [PASS] 无 build: 段
[PASS] ports == ['8000:8000']
[PASS] env DB_PATH == /app/data/insurance.db   [PASS] env PORT == '8000'   [PASS] env TZ == Asia/Shanghai
[PASS] volumes == ['./data:/app/data']   [PASS] restart == unless-stopped
[PASS] 非注释行内无 ${...}（命中 0 行）            ← 极空间兼容性关键
[PASS] compose healthcheck 与 Dockerfile 一致（含 interval/timeout/start_period/retries 30s/5s/10s/3）
```

**卷同时持久化论证**：`settings.attachments_dir` 默认 `./data/attachments`；容器内 `WORKDIR=/app`，故落点为 `/app/data/attachments`；`volumes: ./data:/app/data` 覆盖 `/app/data` 整棵子树 → **`insurance.db` 与 `attachments/` 同卷持久化** ✅。

### A1-3 `docker-compose.build.yml` —— 8/8 PASS

```
[PASS] 有 build: 段   [PASS] context='.'   [PASS] dockerfile=Dockerfile
[PASS] image tag 一致 1.2.0
[PASS] BASE_IMAGE/PIP_INDEX_URL/TZ 均有 ${...:-默认值}
[PASS] volumes == ['./data:/app/data']
```

### A1-4 `.dockerignore` —— **发现 1 处漏网（D1）**

逐条枚举仓库根 **44 个条目**并判断是否进入构建上下文（完整表见 `docs/qa/_qa_docker_audit.txt` A1-4）：

```
[PASS] app / static / requirements.txt 未被误排除（必需项）
[PASS] data / .venv / tests / docs / .workbuddy-ai / scripts / Dockerfile /
       docker-compose*.yml / .dockerignore / start.* / requirements-dev.txt 均已排除
[PASS] 所有 _*.txt / _*.mjs / qa_*.txt / qa_*.py 均已排除
[FAIL] _verify/  →  IN-CONTEXT（漏网）：3459 个文件，约 36.9 MiB
```

- **D1**：`.dockerignore` 的「临时文件」段只写了 `qa_*.txt` / `qa_*.py` / `_*.txt` / `_*.mjs`，**未覆盖目录 `_verify/`**（无扩展名）。`_verify/` 内含上一轮 Puppeteer 验证残留：`node_modules/`（~36.9 MiB）、`tmp.db`、`server.log`、`seed.py`、`package-lock.json` 等。
- **重要澄清（避免误判）**：Dockerfile **只有** `COPY app ./app` / `COPY static ./static` / `COPY requirements.txt ./`，**没有 `COPY . .`**，故 `_verify/` **不会进入镜像**。缺陷性质是**构建上下文膨胀 ~37 MiB**（拖慢构建；若用远程 builder 则多传 37 MiB），**不是镜像污染 / 数据泄露**。
- 另：`_verify/` 亦不在 `.gitignore`，属仓库卫生问题。

### A1-5 构建脚本 —— 7/8 PASS（1 缺陷 D2）

```
[PASS] .sh IMAGE_TAG=1.2.0   [PASS] .sh tar 名 family-insurance-manager-1.2.0.tar
[PASS] .sh PLATFORM=linux/amd64   [PASS] .sh 传 --build-arg BASE_IMAGE
[PASS] .bat IMAGE_TAG=1.2.0   [PASS] .bat tar 名一致   [PASS] .bat PLATFORM=linux/amd64
[PASS] bash -n build-image.sh（rc=0，语法正确）
[INFO] build-image.sh 权限位 = 0o100666（- 无可执行位）   ← D2
```

- **D2**：`scripts/build-image.sh` **无 Unix 可执行位**（`0o666`）。`docs/DEPLOY-ZSPACE.md` 3.2 节已提示先 `chmod +x scripts/build-image.sh`，**但 `README.md` 第 90–91 行「最短命令路径」直接写 `./scripts/build-image.sh`，未提示 chmod** → 按 README 操作会 `Permission denied`。

### A1-6 文档一致性 —— 主要 PASS，2 处轻微偏差（D3/D4）

```
-- DEPLOY-ZSPACE.md：tag/tar/端口/卷/架构/env DB_PATH/PORT/备份含 attachments/ 全部 PASS
[INFO] DEPLOY-ZSPACE 未提及 ATTACHMENTS_DIR（可选；默认在卷内，不影响）
-- README.md：6 个环境变量与 config.py 全部一致（DB_PATH/PORT/STATIC_DIR/ATTACHMENTS_DIR/
   MAX_ATTACHMENT_SIZE/MAX_ATTACHMENTS_PER_POLICY）；API 表含全部新接口  → PASS
-- overview.md：见 D4
```

- **D3（轻微）**：`README.md` 第 141 行 API 表写 `GET/DELETE /api/policies/{id}/attachments/{attId}`，但该路径**只有 `DELETE`**（GET 内容是 `/{attId}/content`，已在第 142 行列出）。建议把第 141 行方法改为 `DELETE`。
- **D4（文档，按指示仅记录）**：`overview.md` 属**超前描述**——既声明「当前版本 v1.2」已完成，又在第 255 行写「前端 v1.2 交互…待实现」（**自相矛盾**）；第 175/200 行测试数写 **163**（当前全量已 **214**，含 QA 新增）；第 237 行称根目录临时文件已被 `.dockerignore` 排除，但**未涵盖 `_verify/`**（即 D1 漏网未反映在文档里）。

### A3 零外部依赖 —— PASS（1 处为我方误报，已澄清）

```
扫描 static/ 下 17 个文件
[PASS] static/ 无 package.json / node_modules / vite.config / webpack / tsconfig.json
```

- 脚本初筛报 `static/favicon.svg` 命中 `http://`。**经人工读回确认：唯一命中是 SVG 的 XML 命名空间 `xmlns="http://www.w3.org/2000/svg"`，是命名空间 URI 而非网络请求** → **A3 实为 PASS**，`static/` 无任何 CDN / 外链 / 构建产物，离线可用。

### A4 用户真实数据不会进镜像 —— PASS

```
data/ 下文件：['data/.gitkeep', 'data/insurance.db']
[PASS] Dockerfile 无 COPY data（data 不会进镜像）
insurance.db md5=bdf59b1d0da092a2553ccb8f99940e4a size=36864   members=1 policies=2
[INFO] policies 含 7 售后列：False（迁移未执行——正常）
[INFO] 含 policy_attachments 表：False（正常）
[INFO] data/attachments/ 存在：False
```

**双重保证**：`.dockerignore` 排除 `data/` + Dockerfile 无 `COPY data`。真实库**未被迁移、未被修改**（md5 与 team-lead 实测一致）。

---

## 3. A2 依赖完整性（**本阶段最有价值的一步**）—— PASS

在**全新临时 venv** 中，**只**用 `requirements.txt`、并加 `--only-binary=:all:` 安装（强制仅 wheel）：

```
[PASS] pip install --only-binary=:all: -r requirements.txt → rc=0
       Successfully installed ... fastapi-0.141.1 ... python-multipart-0.0.20 ...
[PASS] python-multipart wheel = python_multipart-0.0.20-py3-none-any.whl   ← 纯 Python，无需编译
[PASS] 全部依赖均为 wheel（sqlalchemy/pydantic-core/greenlet/httptools/watchfiles/websockets 为 cp313-win_amd64 wheel），无需编译
[PASS] 服务就绪：True
[PASS] GET /api/health → 200            [PASS] GET /api/meta/enums → 200
[PASS] GET /api/members → 200           [PASS] GET /api/policies → 200
[PASS] GET /api/policies/suggestions → 200   [PASS] GET /api/stats/dashboard → 200
[PASS] 启动后自动创建 DB            [PASS] 启动后自动创建 attachments 目录
[PASS] 日志无 ModuleNotFoundError
```

**结论**：`requirements.txt` 即镜像所需**全部**运行依赖，**无遗漏、无需编译**（`pip --only-binary` 全绿）；`lifespan` 的 `_ensure_data_dir` + `_ensure_attachments_dir` 会在启动时自动建目录（等价于容器内 `/app/data` 与 `/app/data/attachments`）。

> ⚠️ 本步在 **Windows + cp313-win_amd64** 上执行；镜像目标为 **linux/amd64 + cp313-manylinux**。两者依赖集合一致（`requirements.txt` 已 `==` 锁定），但 **linux 侧 wheel 未在本机验证**（无 Docker），属未覆盖项。

---

## 4. 缺陷清单（供 team-lead 裁决由谁修）

| 编号 | 严重度 | 文件:位置 | 实际 | 期望 |
|---|---|---|---|---|
| **D1** | 中（构建卫生） | `.dockerignore` 第 42–47 行「临时文件」段 | 根目录 `_verify/`（3459 文件 / ~36.9 MiB）**进入构建上下文** | 补 `_verify/`（或通配 `_*/`）。**注：不进镜像**（无 `COPY .`），仅上下文膨胀 |
| **D2** | 中（可用性） | `scripts/build-image.sh`（权限位） + `README.md` 第 90–91 行 | 脚本 `0o666` 无可执行位；README 直接 `./scripts/build-image.sh` 未提示 chmod | 给脚本可执行位（`git update-index --chmod=+x`）**或** README 补 `chmod +x`（DEPLOY 已有） |
| **D3** | 轻（文档） | `README.md` 第 141 行 | 写 `GET/DELETE /api/policies/{id}/attachments/{attId}`，实际**只有 DELETE** | 方法改为 `DELETE`（GET 内容见第 142 行 `/{attId}/content`） |
| **D4** | 轻（文档，仅记录） | `overview.md` 第 3 / 175 / 200 / 237 / 255 行 | 超前描述 v1.2 完成、自相矛盾、测试数 163、遗漏 `_verify/` | team-lead 已指示：**不改**，由工程师重写 |

> 以上 4 项**均非阻断级**：Dockerfile 与两个 compose 均可用，A2/A3/A4 全绿。若只修一项，优先 **D1**（真实构建开销）。

---

## 5. 真实 HTTP / 真机构建说明

- 本阶段**未做真机构建 / 运行**（本机无 `docker` 等，已确认 NOT FOUND）。
- 唯一真实进程验证：**HEALTHCHECK 命令实起 uvicorn 探活**（§2 A1-1b）与 **A2 全新 venv 起服务打接口**（§3）。
- **真机构建模拟（阶段 B）** 待前端完成后进行。

---

## 6. 未覆盖项 / 已知限制（诚实列出）

1. **真机 `docker build` / `docker run` / `docker save`**：本机无容器工具，**完全未做**。建议在有 Docker 的机器补跑 `docker compose -f docker-compose.build.yml up -d --build`。
2. **linux/amd64 wheel 实际解析**：A2 在 Windows/cp313-win_amd64 上验证；linux 侧 manylinux wheel 未在本机验证。
3. **`tzdata` + `TZ` 真机生效**：仅静态确认 Dockerfile 写法正确；未在 Linux 容器验证 `date` 输出东八区。
4. **SIGTERM 优雅停止**：`exec` 形式静态正确；Windows 无法验证真实信号传递。
5. **极空间固件兼容性**（相对路径卷、Compose 变量替换）：无法在本机验证，仅按文档与「非注释行 0 个 `${}`」静态确认。
6. **镜像体积**：`DEPLOY-ZSPACE.md` 3.4 估「约 120–200 MB」为**估算**，未实测（`docker save` 未压缩通常偏大），非硬缺陷。
7. **前端 E2E / 最终代码上的完整构建模拟 / 旧库冷启动升级实测**：属**阶段 B**，待前端完成。

---

## 7. 证据与产物

| 类型 | 路径 |
|---|---|
| 静态审计脚本（只读） | `docs/qa/qa_docker_audit.py` |
| 静态审计输出 | `docs/qa/_qa_docker_audit.txt` |
| 依赖完整性脚本 | `docs/qa/qa_docker_deps_check.py` |
| 依赖完整性输出 | `docs/qa/_qa_docker_deps.txt` |

**未修改任何 `app/` / `static/` / `data/` 内容**；仅新增 QA 脚本与证据文件（均为 `qa_*.py` / `_*.txt`，已被 `.dockerignore` 排除）。
