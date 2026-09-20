; FileButler 安装脚本（Inno Setup 6）— 英文向导版
; 说明：本机 Inno Setup 6.7.3 已自带 ChineseSimplified.isl，中文向导请用 FileButler.iss
;       （已验证可编译）。本文件保留作英文发行版；两份脚本的 MyAppVersion 需手工同步。
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
OutputBaseFilename=FileButler-Setup-{#MyAppVersion}-en
PrivilegesRequired=lowest

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional tasks:"

[Files]
Source: "dist\FileButler\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch FileButler"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Kill FileButler before uninstall so locked files don't leave residue
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM FileButler.exe"; Flags: runhidden
