; FileButler 安装脚本（Inno Setup 6）
;
; 前置步骤：
;   1. cd frontend && npm run build
;   2. .venv\Scripts\python -m PyInstaller --noconfirm --onedir --windowed --name FileButler ^
;        --add-data "frontend/dist;frontend/dist" --collect-all webview main.py
; 编译安装器（需安装 Inno Setup 6）：
;   ISCC.exe FileButler.iss
; 产物：installer\FileButler-Setup-1.1.0.exe

#define MyAppName "FileButler"
#define MyAppVersion "1.1.4"
#define MyAppExeName "FileButler.exe"

[Setup]
AppId={{7F3A2B4C-5D6E-4F8A-9B0C-1D2E3F4A5B6C}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=FileButler
DefaultDirName={autopf}\FileButler
DefaultGroupName=FileButler
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=installer
OutputBaseFilename=FileButler-Setup-{#MyAppVersion}
PrivilegesRequired=lowest

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："

[Files]
Source: "dist\FileButler\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 FileButler"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; 卸载前强制结束 FileButler 进程，避免文件被占用导致卸载残留
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM FileButler.exe"; Flags: runhidden; RunOnceId: "KillApp"
