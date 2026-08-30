; ============================================================
;  AVCAD Windows 安装程序脚本（Inno Setup 6）
;  用 packaging\windows\build.bat 自动调用；也可手动用 Inno Setup 编译。
;  产出：dist\AVCAD-Setup-1.0.0.exe
; ============================================================

#define MyAppName      "AVCAD"
; 版本号可被命令行覆盖：ISCC.exe /DMyAppVersion=1.2.0 AVCAD.iss
; （CI 会从仓库根目录的 VERSION 文件读取后传进来）
#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif
#define MyAppPublisher "Bailey / EZPRO"
#define MyAppURL       ""
#define MyAppExeName   "AVCAD.exe"

[Setup]
AppId={{7F2C4E10-6A3D-4C58-9E7B-2D15A80F4C63}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
VersionInfoVersion={#MyAppVersion}
DefaultDirName={autopf}\AVCAD
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
DisableDirPage=no
LicenseFile=
OutputDir=..\..\dist
OutputBaseFilename=AVCAD-Setup-{#MyAppVersion}
SetupIconFile=..\AVCAD.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
WizardResizable=no
; 兼容 32/64 位：不限制架构，按本机 Python 打包出的 exe 原样安装
PrivilegesRequiredOverridesAllowed=dialog
CloseApplications=yes
RestartApplications=no

[Languages]
; 用仓库自带的简体中文语言文件（CI 上 choco 装的 Inno Setup 可能没有 Languages 目录）
Name: "chinesesimp"; MessagesFile: "languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加图标:"; Flags: unchecked

[Files]
Source: "..\..\dist\AVCAD\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}";        Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}";   Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}";  Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即启动 AVCAD"; Flags: nowait postinstall skipifsilent

[Messages]
chinesesimp.SelectDirDesc=安装向导将把 AVCAD 安装到以下位置。
chinesesimp.FinishedLabel=安装完成。AVCAD 会在启动后自动打开浏览器界面。
