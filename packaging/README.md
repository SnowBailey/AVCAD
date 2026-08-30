# AVCAD macOS 打包说明

## 交付物

| 文件 | 说明 |
|---|---|
| `dist/AVCAD-1.0.0-macOS.dmg` | **可分发的安装盘**（31 MB，含 `AVCAD.app` + `Applications` 快捷方式 + 安装说明） |
| `dist/AVCAD.app` | 应用本体（64 MB，PyInstaller 目录模式，arm64） |

## 一键重新打包

```bash
cd /Users/mac/WorkBuddy/2026-08-28-09-05-56/avcad
bash packaging/build_app.sh      # 生成 dist/AVCAD.app（含图标生成、ad-hoc 签名）
bash packaging/build_dmg.sh 1.0.0  # 生成 dist/AVCAD-1.0.0-macOS.dmg
```

依赖：`pip install pyinstaller pillow`（已装在 avcad venv）。

## 文件说明

| 文件 | 作用 |
|---|---|
| `packaging/avcad_app.py` | 应用入口：启动本地服务（复用 `avcad.ui.app.Handler`）+ 打开浏览器 + Tk 控制面板 |
| `packaging/make_icon.py` | 生成 `AVCAD.icns`（深空底 + 青紫渐变环 + AV 字样） |
| `packaging/build_app.sh` | PyInstaller 打包脚本（图标、数据文件、隐藏依赖、ad-hoc 签名） |
| `packaging/build_dmg.sh` | 把 `.app` 打成 UDZO 压缩的 DMG |
| `packaging/smoke_test.py` | 对已打包应用跑功能冒烟（首页 / 解析 / 图例库 / 架构 / 出图 / 导出 DXF） |

## 关键设计

1. **图例库写到用户目录**：装在 `/Applications` 后应用目录可能只读，因此启动时把内置图例库复制到
   `~/Library/Application Support/AVCAD/legend_library.json`，之后所有维护都落在这里。
   通过环境变量 `AVCAD_LEGEND_LIBRARY` 覆盖（`legend_store.py` 已支持）。
2. **端口自动选择**：从 8900 起找第一个空闲端口；`allow_reuse_address = False`，端口被占用直接报错而不是静默抢端口。
3. **跳过反向 DNS**：`http.server` 默认在 `server_bind` 里执行 `socket.getfqdn()`，部分网络环境会卡几十秒，
   表现为「应用启动了但页面打不开」。已重写 `server_bind`，直接用 IP 作为 `server_name`。
4. **编码完整性**：必须加 `--collect-submodules encodings`，否则冻结环境缺 `utf-8-sig`，上传 CSV/Excel 解析会 500。
5. **数据文件**：`avcad/ui/static`、`avcad/data`、`avcad/config` 三个目录整体打进包（`--add-data` 用绝对路径）。
6. **`specs.py` 路径规范化**：`DATA_DIR` 必须 `os.path.normpath(...)`。PyInstaller 冻结后 `__file__` 指向归档内虚拟路径，
   中间目录（如 `avcad/model`）在磁盘上不存在，带 `..` 的路径会 ENOENT，导致「找不到 device_specs」。

## 调试开关（环境变量）

| 变量 | 作用 |
|---|---|
| `AVCAD_NO_BROWSER=1` | 不自动打开浏览器 |
| `AVCAD_NO_GUI=1` | 不显示 Tk 控制面板，纯命令行模式（自动化用） |
| `AVCAD_DEBUG_LOG=1` | 把 stdout/stderr 与启动诊断写入 `~/Library/Application Support/AVCAD/launch.log` |

示例：

```bash
AVCAD_NO_GUI=1 AVCAD_NO_BROWSER=1 dist/AVCAD.app/Contents/MacOS/AVCAD
```

## 已验证

- 从 DMG 挂载后直接运行：服务正常、首页可访问
- 功能冒烟（打包后）：首页 / 解析（10 模块）/ 图例库 30 条 / 架构 10 个 / 出图 SVG 29 KB（重叠 0、斜线 0、含线型说明）/ 导出 DXF 80 KB —— 全部 PASS
- GUI（Tk 面板）模式可正常启动，关闭窗口即停止服务
- 开发环境未受影响：pytest 84 passed，4 套 e2e 全 PASS

## 用户侧注意事项

- 首次打开若提示「无法验证开发者」：右键 `AVCAD.app` →「打开」→ 再点「打开」。
  （当前是 ad-hoc 签名；正式分发建议用 Apple Developer ID 签名并公证。）
- 应用是本地服务 + 浏览器界面，关闭 Tk 窗口即停止服务。
- 图例库（永久文档）位置见上方第 1 条，可单独备份。
