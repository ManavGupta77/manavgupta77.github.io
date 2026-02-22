--- CUT HERE ---
@echo off
set OUT=all_source_files.txt
if exist %OUT% del %OUT%
for /r src %%f in (*.py) do (
    echo ======================================== >> %OUT%
    echo FILE: %%f >> %OUT%
    echo ======================================== >> %OUT%
    type "%%f" >> %OUT%
    echo. >> %OUT%
)
echo Done.