# 无 Docker 组装镜像（offline-image-build）

本目录下的 5 个脚本，可以在**完全没有安装任何容器工具**的电脑上，
按 `docker save` 的经典格式**直接组装**出一个可 `docker load` 的镜像 tar。

用途：当电脑装不了 Docker（例如 WSL 被安全策略禁用、或不想为一次打包装 1.5GB 的 Docker Desktop）时，
仍然能产出交付给 NAS 的镜像文件。

## 前提

- Python 3.13（本项目 `.venv` 即可）
- 能访问 PyPI 国内镜像源（脚本默认用清华源）
- 能访问 Docker 镜像站（脚本默认 `docker.m.daocloud.io`，回退 `docker.1ms.run`、`hub.rat.dev`）

## 执行顺序

在**项目根目录**依次运行（每步都会打印结果，可重复执行，已下载的会走缓存）：

```bash
.venv/Scripts/python.exe tools/offline-image-build/step1_pull_base.py
.venv/Scripts/python.exe tools/offline-image-build/step2_wheels.py
.venv/Scripts/python.exe tools/offline-image-build/step3_assemble.py
.venv/Scripts/python.exe tools/offline-image-build/step4_verify.py
.venv/Scripts/python.exe tools/offline-image-build/step5_final_audit.py
```

（macOS / Linux 把 `.venv/Scripts/python.exe` 换成 `.venv/bin/python`）

| 步骤 | 做什么 | 产物 |
|---|---|---|
| `step1_pull_base.py` | 经镜像站拉取官方 `python:3.13-slim` 的 **linux/amd64** config 与 4 个层；校验每层解压后 SHA256 == 官方 `rootfs.diff_ids` | `<工作目录>/base/` |
| `step2_wheels.py` | 用 `pip download --only-binary=:all: --platform manylinux*_x86_64 --python-version 3.13` 下载**预编译 wheel**（不需要编译器），再 `pip install --target` 到层目录 | `<工作目录>/layer_deps/` |
| `step3_assemble.py` | 生成依赖层/应用层，写出 config（ENV/WORKDIR/CMD/EXPOSE/LABEL/HEALTHCHECK 全对齐 `Dockerfile`）+ `manifest.json` + `repositories` | `dist/family-insurance-manager-<版本>-amd64.tar` |
| `step4_verify.py` | **结构校验** 85 项：tar 结构、层摘要 vs `diff_ids`、叠加后文件系统、import 完整性、控制台脚本、config 字段、history 与层数关系、每层三件套 | `<工作目录>/verify_result.json` |
| `step5_final_audit.py` | **内容审计** 21 项：ELF 架构、应用层字节级完整性、层叠加冲突 | `<工作目录>/audit_result.json` |

两套校验合计 **106 项，全部 PASS**。

## 输出格式

采用 `docker save` 的**经典格式**（兼容 Docker 1.x ~ 27.x，也兼容各 NAS 的图形化导入）：

```
<layer-diff-id>/VERSION
<layer-diff-id>/layer.tar        ← 未压缩 tar（与 docker save 一致）
...
<config-sha256>.json
manifest.json
repositories
```

层顺序：4 个官方基础层 → 依赖层（site-packages + `/usr/local/bin` 控制台脚本）→ 应用层（`/app/app`、`/app/static`、`/app/data`）。

## 四个必须知道的实现细节

1. **控制台脚本要自己生成**。`pip install --target` 在 Windows 上生成的是 `uvicorn.exe`（Windows 启动器），
   在 Linux 容器里没用。脚本会解析各包的 `entry_points.txt`，按 pip 在 Linux 上的格式重新生成
   带 `#!/usr/local/bin/python3.13` shebang 的 POSIX 脚本。
2. **不要把 Windows 的 `.pyd` 打进镜像**。用 `--platform manylinux*_x86_64` 强制下载 Linux wheel，
   解出来的 `.so` 应是 `*.cpython-313-x86_64-linux-gnu.so`。
3. **`tzdata` 那一步是空操作**。经核实 `python:3.13-slim` 基础镜像**自带** `/usr/share/zoneinfo`
   （443 个真实 TZif 文件），所以 `apt-get install tzdata` 不会新增任何文件；
   脚本只复刻该步真正生效的两条：`/etc/localtime` 改指 `Asia/Shanghai` + 写 `/etc/timezone`。
   也正因如此，**不安装 PyPI 的 `tzdata` 包**（`Dockerfile` 里没有它）。
4. **`history` 的非空条目数不能多于层数**。这条是**最容易被忽略、一旦踩中就只在导入时炸**的坑，
   而且判据不是"必须相等"（我一开始就理解错了，特此更正）——moby `image/store.go` 的 `Create()` 原文是：

   ```go
   layerCounter := 0
   for _, h := range img.History { if !h.EmptyLayer { layerCounter++ } }
   if layerCounter > len(img.RootFS.DiffIDs) {
       return "", errdefs.InvalidParameter(errors.New("too many non-empty layers in History section"))
   }
   ```

   即**「不得多于」**，等于或更少都能通过。而 `tarexport/load.go` 在 **`manifest.json` 存在时必然调用
   `l.is.Create(config)`**，所以超了就是硬失败（在 NAS 上表现为笼统的「任务添加失败」）。

   初版脚本把 Dockerfile 的 8 条指令全写成"产生文件系统的层"，得到 **11 条非空 history 却只有 6 个层**
   → 必然失败。正确做法：只有真正对应一个层的指令才不标 `empty_layer`，其余全标。
   `step3` 现在会在写出前自检，**超出层数就 `SystemExit`**，绝不产出坏镜像。

   另外 `load.go` 还有一条硬校验：`len(manifest.Layers) != len(rootfs.diff_ids)` 直接报
   `invalid manifest, layers length mismatch`——本方案两者都是 6，已通过。

## 与真实 `docker save` 的逐项对齐（v3 修正）

镜像 tar 的每层目录按经典格式应为 **`VERSION` + `json` + `layer.tar` 三件套**。三者的内容：

| 文件 | 内容 |
|---|---|
| `<层摘要>/VERSION` | 固定 `1.0` |
| `<层摘要>/json` | legacy v1 层元数据：`{"id","parent","created","container_config","config","architecture","os"}` |
| `<层摘要>/layer.tar` | 该层的文件系统差分，**未压缩** tar |

> ⚠️ 注意**什么才是真正必需的**（我一开始判断错了，这里更正）：
> `docker load` 在 **`manifest.json` 存在时走现代路径**（`tarexport/load.go`），
> 只读 `manifest.json` 指到的 `Config` 与各 `Layers` 文件，**不读 `<层>/json`，也不读 `VERSION`**。
> `<层>/json` 只在 **没有 `manifest.json` 的 legacy 路径**下才被读取
> （`legacyLoad` → `legacyLoadImage` 读 `<oldID>/json`，并用 `v1.HistoryFromConfig` 重建历史）。
> 所以补 `json` 属于**保真度加固**（兼容那些自己按经典格式解析的第三方导入器），
> **不是**导致导入失败的根因——根因见上一节的第 4 条。

根目录三个文件：

| 文件 | 内容 |
|---|---|
| `<config-sha256>.json` | 镜像 config；文件名必须等于其内容的 sha256（镜像 ID 自洽） |
| `manifest.json` | `[{"Config","RepoTags","Layers"}]`，`Layers` 指向 `<层摘要>/layer.tar` |
| `repositories` | `{"镜像名":{"标签":"<最上层摘要>"}}` —— 值取**最上层**，与 moby `save.go` 的 `imageDescr.layers[len-1]` 一致 |

> 这些结论来自直接读 moby 的 `image/tarexport/save.go`、`image/tarexport/load.go`、
> `image/store.go` 源码，不是猜的。

## step5 为什么值得单独存在

`step4` 查「结构对不对」，`step5` 查「内容是不是真的能用」。三块都是**手工组装才会引入、
且只在真机启动瞬间才暴露**的风险：

| 审计项 | 抓什么 | 不查会怎样 |
|---|---|---|
| **F. ELF 架构** | 遍历镜像内全部 ELF（实测 766 个），要求 `e_machine == 62 (x86-64)`；并抽查 7 个关键 C 扩展确实存在 | wheel 是手工按平台挑的，混入一个 aarch64 的 `.so` 就会在容器里以 `cannot open shared object` 炸掉 |
| **G. 字节级完整性** | 应用层每个文件与仓库源文件逐一比对 SHA256（实测 40 个文件全一致） | tar 组装时的静默截断/编码损坏，静态结构检查完全看不出来 |
| **H. 层叠加冲突** | 逐层叠加，检查后层是否意外覆盖前层、层内是否有重复路径 | `pip install --target` 在多包文件重名时是「后写覆盖」，可能悄悄盖掉基础层文件 |

> 写这个脚本时踩到的两个自身 bug，一并记下以免重犯：
> ① 用「ELF 文件最多的层」猜依赖层会猜中**基础层**（基础层 655 个 ELF，依赖层只有 14 个），
> 必须改用「含 `usr/local/bin/uvicorn` 的层」来定位；
> ② 判断「名为 `.so` 却不是 ELF」时必须用严格后缀正则 `\.so(\.\d+)*$`，
> 否则 `etc/ld.so.cache`、`etc/ld.so.conf`、`libstdc++.so.6.0.33-gdb.py` 全是误报。

## 局限（诚实声明）

- 这些脚本**无法替代真实的 `docker build`**：它不执行 `RUN` 指令，只是按 `Dockerfile` 的**最终效果**逐项复刻。
  因此本目录的产物与 `Dockerfile` 构建的镜像**功能等价**，但层的历史/ID 不同。
- **没有容器可以 `docker load` 验证**。`step4` + `step5` 覆盖了格式、层摘要、文件系统结构、依赖完整性、
  ELF 架构、字节级一致性、层叠加冲突，但「真实导入 + 启动」必须在有 Docker 的机器或目标 NAS 上完成。
- 脚本默认把**仓库根目录**按「脚本所在目录的上两级」推断，中间产物写到**系统临时目录**下的 `_imgbuild/`。
  如需指定位置，用环境变量覆盖：`IMGBUILD_REPO`（仓库根）、`IMGBUILD_WORK`（工作目录）。
