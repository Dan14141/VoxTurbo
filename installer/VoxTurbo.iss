#if VER != EncodeVer(6, 7, 1)
  #error This installer must be built with Inno Setup 6.7.1.
#endif

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#ifndef SourceDir
  #define SourceDir SourcePath + "..\dist\VoxTurbo"
#endif
#ifndef ReleaseDir
  #define ReleaseDir SourcePath + "..\dist\installer"
#endif
#ifndef LicenseFile
  ;; scripts/build.ps1 выкладывает текст LICENSE в этот .txt файл: Inno показывает
  ;; страницу лицензии, а сам текст дополнительно попадает в {app}\licenses\VoxTurbo.
  #define LicenseFile SourcePath + "..\build\licenses\VoxTurbo\LICENSE.txt"
#endif
#if !FileExists(LicenseFile)
  #error Текст лицензии не подготовлен. Соберите установщик через scripts/build.ps1.
#endif

[Setup]
AppId={{6E4AEFCB-7FC4-4C94-96EE-B957B03D7C36}
AppName=VoxTurbo
AppVersion={#AppVersion}
AppVerName=VoxTurbo {#AppVersion}
AppPublisher=VoxTurbo
VersionInfoVersion={#AppVersion}
VersionInfoDescription=VoxTurbo — локальная диктовка для Windows 10
LicenseFile="{#LicenseFile}"
DefaultDirName={localappdata}\Programs\VoxTurbo
DefaultGroupName=VoxTurbo
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19045
OutputDir={#ReleaseDir}
OutputBaseFilename=VoxTurbo-Setup-{#AppVersion}
SetupIconFile={#SourcePath}voxturbo.ico
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\VoxTurbo.exe
UninstallFilesDir={app}
UsePreviousAppDir=yes
CloseApplications=yes
CloseApplicationsFilter=VoxTurbo.exe
RestartApplications=no
SetupLogging=yes

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Дополнительные ярлыки:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\VoxTurbo"; Filename: "{app}\VoxTurbo.exe"; WorkingDir: "{app}"; Check: not WizardNoIcons
Name: "{userdesktop}\VoxTurbo"; Filename: "{app}\VoxTurbo.exe"; WorkingDir: "{app}"; Tasks: desktopicon; Check: not WizardNoIcons

[Run]
Filename: "{app}\VoxTurbo.exe"; Description: "Запустить VoxTurbo"; Flags: nowait postinstall skipifsilent

; User models/settings are outside {app} and are intentionally preserved.
; No registry autorun, file associations, firewall rules or elevated services.
