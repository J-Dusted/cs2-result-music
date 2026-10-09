@echo off
setlocal
cd /d "%~dp0"

if not exist "bin" mkdir "bin"

set COMMON_FLAGS=/nologo /std:c++20 /EHsc /O2 /MT /W4 /utf-8 /DUNICODE /D_UNICODE ^
 /D_CRT_SECURE_NO_WARNINGS /I"src\common" /I"src\vst"

echo Building CS2MicInjector.dll ...
cl %COMMON_FLAGS% /LD "src\common\command_parser.cpp" "src\vst\mic_injector_core.cpp" ^
 "src\vst\vst2_plugin.cpp" /Fe:"bin\CS2MicInjector.dll" ^
 /link /OUT:"bin\CS2MicInjector.dll" mfplat.lib mfreadwrite.lib mfuuid.lib ole32.lib
if errorlevel 1 exit /b %errorlevel%

echo Building CS2LocalPlayback.exe ...
cl %COMMON_FLAGS% "src\common\command_parser.cpp" "src\player\cs2_local_playback.cpp" ^
 /Fe:"bin\CS2LocalPlayback.exe" ^
 /link /SUBSYSTEM:WINDOWS /OUT:"bin\CS2LocalPlayback.exe" ^
 mfplat.lib mfreadwrite.lib mfuuid.lib ole32.lib winmm.lib shell32.lib
if errorlevel 1 exit /b %errorlevel%

echo Building native tests ...
cl %COMMON_FLAGS% "src\common\command_parser.cpp" "src\vst\mic_injector_core.cpp" ^
 "src\vst\native_tests.cpp" /Fe:"bin\CS2MicInjectorTests.exe" ^
 /link /SUBSYSTEM:CONSOLE /OUT:"bin\CS2MicInjectorTests.exe" ^
 mfplat.lib mfreadwrite.lib mfuuid.lib ole32.lib
if errorlevel 1 exit /b %errorlevel%

echo Build complete.
endlocal
