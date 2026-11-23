# Build the Windows download: one file, Q2Emulator.exe.
#
#   powershell -ExecutionPolicy Bypass -File build.ps1 -Project <this project> -Out <folder to make> [-Work <scratch folder>]
#
# What it makes: <Out>\Q2Emulator.exe, the launcher (launcher.cs) with its data packed inside it. The
# launcher unpacks that into a "data" folder beside itself the first time it runs:
#   data\linux.tar.gz     the emulator's private Linux: Alpine + what setup-rootfs.sh installs
#   data\app\             the emulator itself (copied into the private Linux by the launcher)
#   data\version.txt      a hash of data\app: when it changes, the launcher copies the app again
#   data\Uninstall.cmd    removes the emulator from the PC again (as More > Remove... does)
#
# Needs WSL 2 on the build machine and the internet (Alpine and its packages are downloaded).
# The emulator's QEMU (<Project>\bin\qemu-mipsel-static) and compiled shim (emulator\libq2emu.so,
# from tools\build_shim.py) must already be there.
param(
    [Parameter(Mandatory = $true)][string]$Project,
    [Parameter(Mandatory = $true)][string]$Out,
    [string]$Work = (Join-Path $env:TEMP 'q2emulator-build')
)
$ErrorActionPreference = 'Stop'
$AlpineVersion = '3.24.4'
$AlpineSha256 = 'cdaa38cb8de3537967ee5b6c9b6cd00cc962da2b018e6eea48696bb274923012'
$AlpineFile = "alpine-minirootfs-$AlpineVersion-x86_64.tar.gz"
$AlpineUrl = "https://dl-cdn.alpinelinux.org/alpine/v$($AlpineVersion.Substring(0, $AlpineVersion.LastIndexOf('.')))/releases/x86_64/$AlpineFile"
$Build = 'Q2EmulatorBuild'   # a throwaway WSL distribution, removed at the end

function Step($text) { Write-Host "== $text" }
function Wsl { & wsl.exe @args; if ($LASTEXITCODE) { throw "wsl $args failed ($LASTEXITCODE)" } }

foreach ($need in "$Project\bin\qemu-mipsel-static", "$Project\emulator\libq2emu.so", "$Project\emulator\libq2emu.so.sha256") {
    if (-not (Test-Path $need)) { throw "missing: $need" }
}
New-Item -ItemType Directory -Force $Work, $Out | Out-Null
$Pack = Join-Path $Work 'pack'   # what goes inside the .exe
Remove-Item -Recurse -Force $Pack -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $Pack | Out-Null

Step "Alpine $AlpineVersion"
$alpine = Join-Path $Work $AlpineFile
if (-not (Test-Path $alpine)) { Invoke-WebRequest -UseBasicParsing $AlpineUrl -OutFile $alpine }
if ((Get-FileHash $alpine -Algorithm SHA256).Hash.ToLower() -ne $AlpineSha256) { throw "checksum mismatch: $alpine" }

Step 'The private Linux'
cmd /c "wsl.exe --unregister $Build >nul 2>nul"   # a leftover from an interrupted build
$distro = Join-Path $Work 'build-distro'
Remove-Item -Recurse -Force $distro -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $distro | Out-Null
Wsl --import $Build $distro $alpine --version 2 | Out-Null
Copy-Item "$Project\packaging\setup-rootfs.sh" $Work -Force
$env:Q2SETUP = Join-Path $Work 'setup-rootfs.sh'
$env:WSLENV = 'Q2SETUP/p'
Wsl -d $Build -u root -e sh -c 'sh "$Q2SETUP"'
Wsl --terminate $Build | Out-Null
$linux = "$Pack\linux.tar.gz"
Remove-Item $linux -ErrorAction SilentlyContinue
Wsl --export $Build $linux --format tar.gz | Out-Null
cmd /c "wsl.exe --unregister $Build >nul 2>nul"
Remove-Item -Recurse -Force $distro -ErrorAction SilentlyContinue

Step 'The emulator'
$app = "$Pack\app"
Remove-Item -Recurse -Force $app -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force "$app\emulator", "$app\bin" | Out-Null
Copy-Item "$Project\emulator\*.py", "$Project\emulator\shim.c", "$Project\emulator\libq2emu.so", "$Project\emulator\libq2emu.so.sha256", "$Project\emulator\icon.png", "$Project\emulator\q2emulator.desktop" "$app\emulator"
Copy-Item "$Project\bin\qemu-mipsel-static" "$app\bin"
if (Test-Path "$Project\bin\count\q2count-run") { Copy-Item -Recurse "$Project\bin\count" "$app\bin\count" }  # the measuring mode (tools/build_count.sh)
Copy-Item "$Project\LICENSE" $app
Copy-Item "$Project\packaging\install.sh" $app   # the launcher's install step: the copy and the drive lock
Copy-Item "$Project\docs\THIRD-PARTY.md" $app   # what the download bundles, and under which licenses
$hashes = Get-ChildItem $app -Recurse -File | Sort-Object FullName | ForEach-Object { (Get-FileHash $_.FullName -Algorithm SHA256).Hash }
$sha = [Security.Cryptography.SHA256]::Create()
$version = ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::ASCII.GetBytes($hashes -join ''))) -replace '-', '').Substring(0, 16).ToLower()
Set-Content "$Pack\version.txt" $version -Encoding ascii
Add-Type -AssemblyName System.IO.Compression.FileSystem
[IO.Compression.ZipFile]::CreateFromDirectory($app, "$Pack\app.zip")

Step 'Q2Emulator.exe'
$csc = "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
$src = Join-Path $Work 'launcher.cs'; $ico = Join-Path $Work 'icon.ico'
Copy-Item "$Project\packaging\launcher.cs" $src -Force
Copy-Item "$Project\packaging\icon.ico" $ico -Force
& $csc /nologo /target:winexe /optimize+ "/out:$Out\Q2Emulator.exe" "/win32icon:$ico" /r:System.Windows.Forms.dll /r:System.Drawing.dll `
    /r:System.IO.Compression.dll /r:System.IO.Compression.FileSystem.dll `
    "/resource:$Pack\linux.tar.gz,linux.tar.gz" "/resource:$Pack\app.zip,app.zip" "/resource:$Pack\version.txt,version.txt" $src
if ($LASTEXITCODE) { throw 'the launcher did not compile' }

$size = (Get-ChildItem $Out -Recurse -File | Measure-Object Length -Sum).Sum
Step ("Done: $Out  ({0:N0} MB, app version $version)" -f ($size / 1MB))
