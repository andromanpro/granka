#ifndef PayloadDir
  #error PayloadDir is required
#endif
#ifndef AppVersion
  #error AppVersion is required
#endif
#ifndef ArtifactDir
  #error ArtifactDir is required
#endif
#ifndef OutputName
  #define OutputName "Granka-" + AppVersion + "-Setup-x64"
#endif

[Setup]
AppId={{247B6618-4778-4CBE-B529-8EDC5ED0BA10}
AppName=Гранка
AppVersion={#AppVersion}
AppPublisher=androman (androman.pro)
AppPublisherURL=https://androman.pro
AppSupportURL=https://github.com/andromanpro/granka/issues
DefaultDirName={localappdata}\Programs\Granka
DefaultGroupName=Гранка
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
UninstallDisplayIcon={app}\Granka.exe
AppMutex=Local\GrankaInstalledServer
CloseApplications=no
RestartApplications=no
SetupIconFile={#PayloadDir}\app\static\brand\granka.ico
LicenseFile={#PayloadDir}\app\LICENSE
OutputDir={#ArtifactDir}
OutputBaseFilename={#OutputName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
DisableWelcomePage=no
SetupLogging=yes

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Ярлыки:"; Flags: unchecked

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Гранка"; Filename: "{app}\Granka.exe"; WorkingDir: "{app}"
Name: "{group}\Войти в ChatGPT"; Filename: "{app}\Granka.exe"; Parameters: "--login"; WorkingDir: "{app}"
Name: "{group}\Остановить Гранку"; Filename: "{app}\Granka.exe"; Parameters: "--stop"; WorkingDir: "{app}"
Name: "{autodesktop}\Гранка"; Filename: "{app}\Granka.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\Granka.exe"; Description: "Открыть Гранку"; Flags: nowait postinstall skipifsilent
