# System integration only: create or update the desktop launcher.
$ErrorActionPreference = 'Stop'
$grankaRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$grankaPython = (& py -3 -c 'import sys; print(sys.executable)').Trim()
if ($LASTEXITCODE -ne 0) { throw 'Python is required' }
$grankaPythonw = Join-Path (Split-Path $grankaPython) 'pythonw.exe'
$grankaTitle = [string]([char]0x0413) + [char]0x0440 + [char]0x0430 + [char]0x043D + [char]0x043A + [char]0x0430
$grankaLinkPath = Join-Path ([Environment]::GetFolderPath('Desktop')) ($grankaTitle + '.lnk')
$grankaShell = New-Object -ComObject WScript.Shell
$grankaLink = $grankaShell.CreateShortcut($grankaLinkPath)
$grankaLink.TargetPath = $grankaPythonw
$grankaLink.Arguments = '"' + (Join-Path $grankaRoot 'launch-granka.py') + '"'
$grankaLink.WorkingDirectory = $grankaRoot
$grankaLink.IconLocation = (Join-Path $grankaRoot 'static\brand\granka.ico') + ',0'
$grankaLink.Description = $grankaTitle
$grankaLink.WindowStyle = 7
$grankaLink.Save()
Write-Output $grankaLinkPath
