@echo off
set OUT=all_config_files.txt
if exist %OUT% del %OUT%

for /r config %%f in (*.*) do (
    echo ======================================== >> %OUT%
    echo FILE: %%f >> %OUT%
    echo ======================================== >> %OUT%
    type "%%f" >> %OUT%
    echo. >> %OUT%
)

for /r tests %%f in (*.py) do (
    echo ======================================== >> %OUT%
    echo FILE: %%f >> %OUT%
    echo ======================================== >> %OUT%
    type "%%f" >> %OUT%
    echo. >> %OUT%
)

echo Done. Output: %OUT%