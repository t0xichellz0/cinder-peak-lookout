param([string]$Py, [string]$Dir)
$Dir = (Resolve-Path $Dir).Path
$shell = New-Object -ComObject WScript.Shell
$targets = @([Environment]::GetFolderPath('Desktop'), (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'))
foreach ($t in $targets) {
    $lnk = $shell.CreateShortcut((Join-Path $t 'Cinder Peak Lookout.lnk'))
    $lnk.TargetPath = $Py
    $lnk.Arguments = '"' + (Join-Path $Dir 'launcher.pyw') + '"'
    $lnk.WorkingDirectory = $Dir
    $lnk.IconLocation = (Join-Path $Dir 'icon.ico')
    $lnk.Description = 'Cinder Peak Lookout'
    $lnk.Save()
}
Write-Host 'Done. "Cinder Peak Lookout" is now on your desktop and in the Start menu.'
