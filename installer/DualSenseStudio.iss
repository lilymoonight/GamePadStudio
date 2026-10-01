#define AppVersion "1.9.0"
[Setup]
AppId={{A430C3D2-9278-48A9-BA24-15C59D0A0120}
AppName=DualSense Studio
AppVersion={#AppVersion}
AppPublisher=DualSense Studio Project
DefaultDirName={localappdata}\Programs\DualSenseStudio
DefaultGroupName=DualSense Studio
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
DisableProgramGroupPage=yes
WizardStyle=modern
OutputDir=..\artifacts
OutputBaseFilename=DualSenseStudio-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
UninstallDisplayIcon={app}\DualSenseStudio.exe
SetupIconFile=..\dualsense5\assets\studio.ico
CloseApplications=yes
RestartApplications=no
SetupLogging=yes

[Languages]
Name: "zhcn"; MessagesFile: "ChineseSimplified.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "startup"; Description: "登录 Windows 后自动启动后台映射"; GroupDescription: "后台运行："
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："; Flags: unchecked

[Files]
Source: "..\dist\DualSenseStudio\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\DualSense Studio"; Filename: "{app}\DualSenseStudio.exe"; IconFilename: "{app}\_internal\dualsense5\assets\studio.ico"
Name: "{autodesktop}\DualSense Studio"; Filename: "{app}\DualSenseStudio.exe"; IconFilename: "{app}\_internal\dualsense5\assets\studio.ico"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "DualSenseStudioAgent"; ValueData: """{app}\DualSenseStudio.exe"" --agent"; Tasks: startup; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "DualSenseStudioAgent"; Flags: deletevalue; Check: not WizardIsTaskSelected('startup')

[Run]
Filename: "{app}\DualSenseStudio.exe"; Parameters: "--agent"; Flags: nowait runhidden
Filename: "{app}\DualSenseStudio.exe"; Description: "打开 DualSense Studio"; Flags: nowait postinstall skipifsilent

[Code]
procedure StopInstalledApp();
var
  ResultCode: Integer;
  ExePath: String;
begin
  ExePath := ExpandConstant('{app}\DualSenseStudio.exe');
  if FileExists(ExePath) then begin
    Exec(ExePath, '--exit-ui', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Exec(ExePath, '--agent-command stop', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Sleep(500);
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopInstalledApp();
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  StopInstalledApp();
  Result := True;
end;
