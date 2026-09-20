; FileButler 安装脚本（Inno Setup 6）
;
; 前置步骤：
;   1. cd frontend && npm run build
;   2. .venv\Scripts\python -m PyInstaller --noconfirm FileButler.spec
;      （产物 dist\FileButler\，onedir）
; 编译安装器：
;   "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" FileButler.iss
;   注意 Inno 可能是 per-user 安装，别只查 Program Files
; 产物：installer\FileButler-Setup-<版本>.exe
;
; 版本号需与 backend/__init__.py 的 __version__ 手工同步（英文变体在 FileButler-en.iss）

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

[Code]
var
  DataDirPage: TInputDirWizardPage;
  HadPointer: Boolean;

function PointerFile: String;
begin
  Result := ExpandConstant('{userappdata}\FileButler\datadir.txt');
end;

function DefaultDataDir: String;
begin
  Result := ExpandConstant('{userappdata}\FileButler');
end;

// 去掉末尾反斜杠再比较，否则 "D:\FB" 和 "D:\FB\" 会被当成两个不同位置
function StripTrailing(const S: String): String;
begin
  Result := S;
  while (Length(Result) > 3) and (Copy(Result, Length(Result), 1) = '\') do
    Result := Copy(Result, 1, Length(Result) - 1);
end;

procedure InitializeWizard;
begin
  HadPointer := FileExists(PointerFile);
  DataDirPage := CreateInputDirPage(wpSelectDir,
    '数据存储位置', '索引数据库与缩略图缓存存放在哪里？',
    'FileButler 会把索引数据库（可能长到几百 MB 到几 GB）和缩略图缓存放在下面这个目录。'
    + #13#10 + '默认在 C 盘的用户目录。如果 C 盘空间紧张，可以换到其他盘。'
    + #13#10 + '装完之后也能在应用内「设置 → 数据存储位置」随时迁移，改错了不会丢数据。',
    False, '');
  DataDirPage.Add('数据目录：');
  DataDirPage.Values[0] := DefaultDataDir;
  // 已有指针说明这不是全新安装：绝不能用本次选择去覆盖用户既有的数据位置，
  // 所以只在 CurStepChanged 里靠 HadPointer 判断，页面上不做任何破坏性动作
end;

function NeedWriteDataDir: Boolean;
begin
  Result := (not HadPointer)
    and (CompareText(StripTrailing(DataDirPage.Values[0]), StripTrailing(DefaultDataDir)) <> 0);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  Cmd: String;
begin
  if CurStep <> ssPostInstall then Exit;
  if not NeedWriteDataDir then begin
    if HadPointer then
      Log('检测到已有 datadir.txt，保持用户原有的数据目录设置')
    else
      Log('数据目录沿用默认位置，无需写指针');
    Exit;
  end;
  // 让应用自己写指针（main.py --set-data-dir），安装器不直接落文件：
  // 一是格式归 backend/db.py 所有，不该有两份实现；
  // 二是 Inno 写文本用 ANSI，而 db.get_data_dir() 按 UTF-8 读，
  // 中文路径会读出乱码、isdir 校验失败后静默回落到默认目录。
  Cmd := '--set-data-dir "' + DataDirPage.Values[0] + '"';
  if Exec(ExpandConstant('{app}\FileButler.exe'), Cmd, '', SW_HIDE,
          ewWaitUntilTerminated, ResultCode) then begin
    if ResultCode <> 0 then
      Log('设置数据目录返回码 ' + IntToStr(ResultCode));
  end else
    MsgBox('已安装到 ' + ExpandConstant('{app}') + #13#10
      + '但设置数据目录失败，应用将使用默认位置。'
      + #13#10 + '你可以在「设置 → 数据存储位置」里重新指定。',
      mbInformation, MB_OK);
end;
