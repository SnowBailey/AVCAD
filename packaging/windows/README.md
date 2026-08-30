# AVCAD Windows 打包说明

## 为什么这里不能直接给你 .exe

**PyInstaller 不支持交叉编译**——在 macOS（Apple Silicon / arm64）上无法生成 Windows 的
`.exe`，官方要求「在目标系统上构建」。因此这里提供的是**一键构建包**：
在任意一台 Windows 电脑上双击一个批处理，即可产出安装程序。

打包参数已按 macOS 版踩过的 4 个坑配好（编码、numpy、路径规范化、反向 DNS），
你在 Windows 上只需要跑脚本，不用再调参数。

---

## 一、最简流程（推荐）

1. 把整个 `avcad` 项目目录拷到 Windows 电脑（U 盘 / 网盘 / 共享目录都行）。
2. 确认已装 **Python 3.10 ~ 3.13**（[python.org](https://www.python.org/downloads/windows/)），
   安装时勾选 **Add Python to PATH**。
3. （可选但推荐）安装 **Inno Setup 6**：<https://jrsoftware.org/isdl.php>（免费，几分钟）。
4. 双击运行：

```
packaging\windows\build.bat
```

脚本会自动：建虚拟环境 → 装依赖 → 生成图标 → PyInstaller 打包 → 制作安装程序。

## 二、产出物

| 情况 | 产出 | 位置 |
|---|---|---|
| 装了 Inno Setup 6 | **`AVCAD-Setup-1.0.0.exe`** 安装程序（带开始菜单/桌面快捷方式、可卸载） | `dist\` |
| 没装 Inno Setup | `AVCAD-1.0.0-Windows-Portable.zip` 绿色版（解压后运行 `AVCAD\AVCAD.exe`） | `dist\` |
| 两者都会产出 | `dist\AVCAD\AVCAD.exe` 目录模式程序本体 | `dist\AVCAD\` |

> 选**安装程序**给同事用；选**绿色版**放在 U 盘里随身用。

## 三、使用

- 启动后会自动起本地服务并打开默认浏览器；弹出的 Tk 控制面板显示访问地址。
- **关闭控制面板窗口 = 停止服务**。
- 图例库（永久文档）位置：`%APPDATA%\AVCAD\legend_library.json`
  （首次启动自动从内置库复制，之后每次修改都落在这里，可单独备份）。
- 端口：从 8900 起自动找第一个空闲端口，被占用会在面板上显示实际地址。

## 四、文件清单

| 文件 | 作用 |
|---|---|
| `build.bat` | 一键构建（venv → 依赖 → 图标 → PyInstaller → Inno Setup / ZIP） |
| `AVCAD.iss` | Inno Setup 安装程序脚本（中文界面、开始菜单 + 可选桌面快捷方式） |
| `requirements.txt` | 依赖版本（与 macOS 构建一致，已锁定） |
| `../avcad_app.py` | 跨平台启动器（macOS / Windows / Linux 通用） |

## 五、排障开关（环境变量）

| 变量 | 作用 |
|---|---|
| `AVCAD_NO_BROWSER=1` | 不自动打开浏览器 |
| `AVCAD_NO_GUI=1` | 不显示 Tk 控制面板，纯命令行模式 |
| `AVCAD_DEBUG_LOG=1` | 把启动日志写到 `%APPDATA%\AVCAD\launch.log` |

示例（cmd）：

```bat
set AVCAD_NO_BROWSER=1
dist\AVCAD\AVCAD.exe
```

## 六、已规避的打包坑（脚本里已处理，仅供了解）

1. `--collect-submodules encodings`：否则冻结环境缺 `utf-8-sig`，CSV/Excel 解析报 500。
2. **不能** `--exclude-module numpy`：ezdxf 的 C 扩展 `ezdxf.acc` 依赖它。
3. `avcad/model/specs.py` 的 `DATA_DIR` 用 `os.path.normpath`：冻结后带 `..` 的路径会 ENOENT。
4. 启动器重写 `server_bind`：父类 `socket.getfqdn()` 反向 DNS 会卡几十秒。
5. Windows 上 `--add-data` 分隔符是 `;`，macOS/Linux 是 `:`（脚本已按 Windows 写法）。

## 七、用 GitHub Actions 自动出安装包（推荐，不用找 Windows 电脑）

仓库已配好两个工作流（`.github/workflows/`）：

| 工作流 | 运行环境 | 产出 |
|---|---|---|
| `build-windows.yml` | `windows-latest` | `AVCAD-Setup-<版本>.exe`（Inno Setup）+ `AVCAD-<版本>-Windows-Portable.zip` |
| `build-macos.yml` | `macos-latest` | `AVCAD-<版本>-macOS.dmg` |

推送到 `main` 即触发（也可在 Actions 页面手动 `Run workflow`）；打 `v*` 标签还会自动挂到 Release。

### 一条命令拿到安装包

```bash
bash packaging/ci_build.sh <你的 GitHub 个人访问令牌>
```

脚本会：提交改动 → 推送 main → 轮询 Windows 构建 → 下载产物 → 解包到 `dist/ci`，最后打印出
`AVCAD-Setup-x.y.z.exe` 和便携版 ZIP。

令牌申请：<https://github.com/settings/tokens>
- 经典 token：勾 `repo`
- 细粒度 token：Contents（读+写）、Actions（读）、Metadata（读）

> 仓库：<https://github.com/SnowBailey/AVCAD> —— 本机没有可用凭据（未装 `gh`、无 token、无 SSH key），
> 所以推送这一步需要你提供令牌；拿到令牌后我就能直接跑完并回传安装包。
