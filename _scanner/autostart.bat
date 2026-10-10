@echo off
rem Turns "start the scanner when Windows starts" on, or off if it is already on.
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\OrderBlock Scanner.bat"
if exist "%STARTUP%" goto remove

> "%STARTUP%" echo @echo off
>> "%STARTUP%" echo cd /d "%~dp0"
>> "%STARTUP%" echo start "OrderBlock Scanner" /min cmd /c start.bat --no-browser
echo.
echo  Done. The scanner will now start by itself (minimised) whenever you log in to Windows.
echo  Run autostart.bat again to turn this off.
echo.
pause
exit /b 0

:remove
del "%STARTUP%"
echo.
echo  Auto-start is now OFF. The scanner only runs when you double-click start.bat.
echo.
pause
