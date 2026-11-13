; Inno Setup 6 script. Per-user install (no admin), Start menu entry, uninstaller, relaunch after silent update.
#define AppVer GetEnv("Z1PCB_VERSION")
#if AppVer == ""
  #error Set Z1PCB_VERSION
#endif

[Setup]
AppId={{F3AECEFB-EF88-4C66-AF81-AD74C98E32AA}
AppName=Z1 PCB
AppVersion={#AppVer}
AppVerName=Z1 PCB {#AppVer}
AppPublisher=Josef Huscava
DefaultDirName={localappdata}\Programs\Z1 PCB
DefaultGroupName=Z1 PCB
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=Z1PCB-{#AppVer}-setup
SetupIconFile=..\assets\z1pcb.ico
UninstallDisplayIcon={app}\Z1PCB.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763

[Languages]
Name: "sk"; MessagesFile: "compiler:Languages\Slovak.isl"
Name: "cz"; MessagesFile: "compiler:Languages\Czech.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\Z1PCB\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Z1 PCB"; Filename: "{app}\Z1PCB.exe"
Name: "{group}\{cm:UninstallProgram,Z1 PCB}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Z1 PCB"; Filename: "{app}\Z1PCB.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Z1PCB.exe"; Description: "{cm:LaunchProgram,Z1 PCB}"; Flags: nowait postinstall
