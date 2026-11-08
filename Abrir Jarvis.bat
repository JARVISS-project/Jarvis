@echo off
setlocal
echo Abrindo o JARVIS por texto, sem iniciar captura de voz.
echo Mantenha esta janela aberta enquanto usar o programa.
call "%~dp0jarvis.bat" --ui
set "JARVIS_UI_EXIT=%ERRORLEVEL%"
if not "%JARVIS_UI_EXIT%"=="0" (
    echo O JARVIS nao iniciou ou foi encerrado com erro.
    echo Consulte o manual em docs\manual\README.md e o diagnostico jarvis.bat --doctor.
    pause
)
exit /b %JARVIS_UI_EXIT%
