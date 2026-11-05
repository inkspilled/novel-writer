; Inno Setup 安装脚本 - Novel Writer
; onedir 模式（DLL 形式）：启动快，杀毒误报少
; 策略：免管理员装到用户目录；用户数据在 %APPDATA%\NovelWriter，
; 不随包、卸载不碰（小说稿安全）。
; （注意：smt 项目实际是 admin 装到 Program Files，两边是各自的既定策略，勿互相照搬）

#define MyAppName      "Novel Writer"
#define MyAppVersion   "0.1.0"
#define MyAppPublisher "NovelWriter"
#define MyAppExeName   "NovelWriter.exe"

[Setup]
; AppId 是本应用的唯一标识（与 smt 的 A1B2C3D4-… 不同库），确定后永不再改
AppId={{B1C2D3E4-F5A6-7890-ABCD-EF1234567890}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
; 首发即定终身：以下两项随 0.1.0 发布后禁止变更——升级时 Inno 靠旧版留在注册表
; 的卸载记录（lowest→HKCU）回填原安装路径（含用户自选的 D 盘等自定义目录）；
; 改成 admin 或换默认目录会让升级"失忆"，被装到新默认路径且无权限清理旧版
; （smt 2026-11-04 客户事故即 admin↔lowest 切换所致，详见 smt DEV_GUIDE 版本与发布约定）
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir=output
OutputBaseFilename=NovelWriter-Setup
SetupIconFile=logo.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName=卸载 {#MyAppName}
UninstallFilesDir={app}
Compression=lzma2/ultra64
SolidCompression=yes
; 免管理员：装到 {localappdata}\Programs，全程无 UAC 弹窗（照 smt）
PrivilegesRequired=lowest

[Languages]
; 中文包随仓库走，不依赖本机 Inno 安装了哪些语言
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
chinesesimplified.startmenu=创建开始菜单快捷方式
english.startmenu=Create Start Menu entry

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "startmenu";   Description: "{cm:startmenu}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; onedir 模式：exe + _internal（DLL/pyd/资源）
Source: "dist\NovelWriter\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\NovelWriter\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
; 不打包任何 data/config 运行时文件：打包后用户数据一律写 %APPDATA%\NovelWriter，
; 覆盖安装与卸载都不触碰小说稿（见 app_config.py/_user_data_dir）。

[Icons]
; 开始菜单（受 startmenu 勾选项控制）+ 卸载入口
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: startmenu
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"; Tasks: startmenu
; 桌面快捷方式
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; 完成页勾选启动（文案随所选语言）
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

; 卸载仅删除安装器登记的程序文件；%APPDATA%\NovelWriter 下的项目/配置/日志保留。
