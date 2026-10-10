; Inno Setup 安装脚本 - Novel Writer
; onedir 模式（DLL 形式）：启动快，杀毒误报少

#define MyAppName      "Novel Writer"
#define MyAppVersion   "1.0.0"
#define MyAppPublisher "NovelWriter"
#define MyAppExeName   "NovelWriter.exe"

[Setup]
AppId={{B1C2D3E4-F5A6-7890-ABCD-EF1234567890}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir=output
OutputBaseFilename=NovelWriter-Setup
SetupIconFile=logo.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName=卸载 {#MyAppName}
UninstallFilesDir={app}
Compression=lzma2/ultra64
SolidCompression=yes
PrivilegesRequired=admin

[Languages]
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

[Dirs]
; 运行时数据目录（首次启动自动创建，这里预建避免权限问题）
Name: "{app}\data"
Name: "{app}\config"
Name: "{app}\logs"

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 卸载时清理运行时生成的数据/日志
Type: filesandordirs; Name: "{app}\data"
Type: filesandordirs; Name: "{app}\logs"
