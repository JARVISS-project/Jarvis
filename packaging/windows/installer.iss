#ifndef SourceDir
  #error SourceDir required
#endif
#ifndef OutputDir
  #error OutputDir required
#endif
#ifndef AppRelease
  #error AppRelease required
#endif
#ifndef AppNumericVersion
  #error AppNumericVersion required
#endif
#ifndef SetupIcon
  #error SetupIcon required
#endif
[Setup]
AppId={code:AppIdentity}
UsePreviousLanguage=no
AppName=JARVIS
AppVersion={#AppNumericVersion}
AppVerName=JARVIS {#AppRelease}
AppPublisher=JARVIS
DefaultDirName={localappdata}\Programs\JARVIS
DefaultGroupName={code:AppGroup}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=JARVIS-Setup-{#AppRelease}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\JARVIS.exe
CloseApplications=no
RestartApplications=no
DisableProgramGroupPage=yes
SetupIconFile={#SetupIcon}
InfoBeforeFile={#SourceDir}\_internal\manual\WINDOWS_EXECUTAVEL.md
LicenseFile={#SourceDir}\_internal\licenses\JARVIS\LICENSE
[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\JARVIS"; Filename: "{app}\JARVIS.exe"; Parameters: "{code:ProfileArgs}"; WorkingDir: "{app}"
Name: "{group}\Diagnóstico JARVIS"; Filename: "{app}\JARVIS.exe"; Parameters: "--diagnostic {code:ProfileArgs}"; WorkingDir: "{app}"
[Code]
function AppIdentity(Param: String): String;
begin
  if ExpandConstant('{param:TestInstance|0}') = '1' then Result := 'JARVIS.Desktop.23BO.Test'
  else Result := 'JARVIS.Desktop';
end;
function AppGroup(Param: String): String;
begin
  if ExpandConstant('{param:TestInstance|0}') = '1' then Result := 'JARVIS Sprint23BO Test'
  else Result := 'JARVIS';
end;
function ProfileArgs(Param: String): String;
var Profile: String;
begin
  Result := '';
  Profile := ExpandConstant('{param:TestProfile|}');
  if (ExpandConstant('{param:TestInstance|0}') = '1') and (Profile <> '') then
    Result := '--profile-root "' + Profile + '"';
end;
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    if not UninstallSilent then
      MsgBox('Programa removido. Seus dados foram preservados: configuração, memória, conexões, documentos, modelos/cache e logs em %LOCALAPPDATA%\JARVIS\Desktop. Consulte o manual antes de optar por apagar essa pasta. Perfis personalizados também são preservados.', mbInformation, MB_OK);
end;
