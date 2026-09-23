; Inno Setup script for Image Batch Renamer.
;
; Packages the PyInstaller --onedir output (dist\ImageRenamer\) into a
; single installer .exe. Run from the repo root:
;   ISCC.exe installer\windows.iss /DMyAppVersion=1.0.0
; (the build workflow passes /DMyAppVersion; it defaults below for local
; testing so the script is still valid without that flag.)

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0-dev"
#endif

#define MyAppName "Image Batch Renamer"
#define MyAppPublisher "Ennea Limited"
#define MyAppExeName "ImageRenamer.exe"

[Setup]
; Fixed, randomly-generated once and never changed -- this is what lets
; Windows treat repeat installs as upgrades of the same app rather than
; unrelated installs, and must stay stable across every future release.
AppId={{AB1C4A71-DE8D-446A-8ACA-E818F15EA39F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\ImageRenamer
DefaultGroupName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
; Inno Setup resolves every relative path in this file (OutputDir,
; SetupIconFile, Source below) relative to THIS SCRIPT'S OWN DIRECTORY
; (installer\), not the working directory ISCC.exe was invoked from --
; "installer\output" here would actually create installer\installer\output.
OutputDir=output
OutputBaseFilename=ImageRenamerSetup
SetupIconFile=..\assets\icon.ico
; Shown as an "I accept" page during install.
LicenseFile=..\LICENSE
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Per-user install by default so it doesn't need admin elevation --
; appropriate for a single-purpose internal tool handed out as one file.
PrivilegesRequired=lowest

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
; Everything PyInstaller's --onedir build produced -- the .exe plus its
; supporting _internal\ folder (the ONNX models, Tcl/Tk, etc.). Relative
; to this script's own directory (installer\), hence "..\dist\...".
Source: "..\dist\ImageRenamer\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
