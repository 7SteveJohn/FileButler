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
; 版本号需与 backend/__init__.py 的 __version__ 手工同步

#define MyAppName "FileButler"
#define MyAppVersion "1.2.1"
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

// 读取指针里「生效中」的数据目录；返回 '' 表示没有或读不出来。
// 指针由应用按 UTF-8 写入，Inno 按 ANSI 解码，含中文的路径可能读成乱码；
// 用目录存在性兜底：读出来不是有效目录就当没读到，页面退回默认值。
// （纯 ASCII 路径如 F:\filebutler 不受影响，这正是绝大多数场景）
function ExistingDataDir: String;
var
  Raw: AnsiString;
  S: String;
begin
  Result := '';
  if not HadPointer then Exit;
  if LoadStringFromFile(PointerFile, Raw) then begin
    S := Trim(string(Raw));
    if (S <> '') and DirExists(S) then
      Result := S;
  end;
end;

// 去掉末尾反斜杠再比较，否则 "D:\FB" 和 "D:\FB\" 会被当成两个不同位置
function StripTrailing(const S: String): String;
begin
  Result := S;
  while (Length(Result) > 3) and (Copy(Result, Length(Result), 1) = '\') do
    Result := Copy(Result, 1, Length(Result) - 1);
end;

procedure InitializeWizard;
var
  Existing: String;
begin
  HadPointer := FileExists(PointerFile);
  DataDirPage := CreateInputDirPage(wpSelectDir,
    '数据存储位置', '索引数据库与缩略图缓存存放在哪里？',
    'FileButler 会把索引数据库（可能长到几百 MB 到几 GB）和缩略图缓存放在下面这个目录。'
    + #13#10 + '默认在 C 盘的用户目录。如果 C 盘空间紧张，可以换到其他盘。'
    + #13#10 + '装完之后也能在应用内「设置 → 数据存储位置」随时迁移，改错了不会丢数据。',
    False, DefaultDataDir);
  DataDirPage.Add('数据目录：');
  if HadPointer then begin
    // 更新安装：预填当前生效的数据目录，而不是每次都默认 C 盘——
    // 用户不会一直挪数据库位置，升级时应当原样保持，除非他主动改。
    // 页面只做展示与可选修改，真正写不写指针仍由 EffectiveDataDir 判断
    DataDirPage.Description :=
      '检测到已有数据目录（下面已填好）。直接「下一步」保持不变，'
      + #13#10 + '数据库和缩略图不会被移动或改动。'
      + #13#10 + '只有把目录改成别的位置，安装器才会切换数据目录。';
    Existing := ExistingDataDir;
    if Existing <> '' then
      DataDirPage.Values[0] := Existing
    else
      DataDirPage.Values[0] := DefaultDataDir;
  end else
    DataDirPage.Values[0] := DefaultDataDir;
end;

// 本次安装最终要写入的目录；返回 '' 表示不动指针。
// 原则：页面/参数的值与「现状」相同 = 不动指针，只有主动给了新位置才写。
// 无人值守安装不能拿页面上的值当"用户的选择"：真正无人值守时根本没人做过这个选择；
// 而"以为自己是静默、其实不是"的情况更危险——/VERYSILENT 会被 Git Bash 改写成本地路径，
// 向导于是照样弹出来，页面上留的是坐在屏幕前那个人填的值，跟调用方的意图无关。
// 静默模式只认显式的 /DATADIR= 参数（显式传参 = 明确要迁移，已有指针也生效）。
function EffectiveDataDir: String;
var
  Param, Chosen, Effective: String;
begin
  Result := '';
  Param := ExpandConstant('{param:datadir|}');
  if WizardSilent then
  begin
    if Param <> '' then Result := Param;
    Exit;
  end;
  Chosen := DataDirPage.Values[0];
  if Param <> '' then Chosen := Param;
  // 与「生效中」的目录相同 → 不动指针；无指针时选了默认目录同理，也不用写
  Effective := ExistingDataDir;
  if Effective = '' then
    Effective := DefaultDataDir;
  if CompareText(StripTrailing(Chosen), StripTrailing(Effective)) = 0 then Exit;
  Result := Chosen;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  Cmd, Target: String;
begin
  if CurStep <> ssPostInstall then Exit;
  Target := EffectiveDataDir;
  if Target = '' then begin
    if HadPointer then
      Log('检测到已有 datadir.txt，页面预填了当前数据目录；与现状相同则指针不动')
    else if WizardSilent then
      Log('无人值守安装且未指定 /DATADIR，数据目录沿用默认位置')
    else
      Log('数据目录沿用默认位置，无需写指针');
    Exit;
  end;
  Log('数据目录将设为: ' + Target);
  // 让应用自己写指针（main.py --set-data-dir），安装器不直接落文件：
  // 一是格式归 backend/db.py 所有，不该有两份实现；
  // 二是 Inno 写文本用 ANSI，而 db.get_data_dir() 按 UTF-8 读，
  // 中文路径会读出乱码、isdir 校验失败后静默回落到默认目录。
  Cmd := '--set-data-dir "' + Target + '"';
  if Exec(ExpandConstant('{app}\FileButler.exe'), Cmd, '', SW_HIDE,
          ewWaitUntilTerminated, ResultCode) then begin
    if ResultCode <> 0 then begin
      Log('设置数据目录失败，返回码 ' + IntToStr(ResultCode));
      MsgBox('已安装到 ' + ExpandConstant('{app}') + #13#10
        + '但数据目录 ' + Target + ' 用不了（无法创建或不可写），'
        + '应用将使用默认位置。' + #13#10
        + '你可以在「设置 → 数据存储位置」里重新指定。',
        mbInformation, MB_OK);
    end;
  end else begin
    Log('无法启动 FileButler.exe 设置数据目录');
    MsgBox('已安装到 ' + ExpandConstant('{app}') + #13#10
      + '但设置数据目录失败，应用将使用默认位置。' + #13#10
      + '你可以在「设置 → 数据存储位置」里重新指定。',
      mbInformation, MB_OK);
  end;
end;
