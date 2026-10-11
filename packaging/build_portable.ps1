<#
.SYNOPSIS
    构建 zpyaudio Windows x64 便携版（onedir 文件夹，非安装包、不含 ffmpeg）。

.DESCRIPTION
    1) 用 PyInstaller 按 packaging/zpyaudio.spec 打包到 dist/；
    2) 把 PORTABLE-README.txt 复制到产物文件夹根（与 zpyaudio.exe 同级）；
    3) 检查产物里没有 ffmpeg，并打印体积与关键文件清单。

    产物：dist/zpyaudio-<版本>-win.amd64-portable/
            zpyaudio.exe        （GUI 子系统，双击启动）
            _internal/          （Python 运行时 + Qt + numpy/scipy 等依赖）
            PORTABLE-README.txt

.PARAMETER Python
    用于打包的 Python 解释器（需已安装 pyinstaller 与 pyinstaller-hooks-contrib）。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File packaging\build_portable.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File packaging\build_portable.ps1 -Python C:\miniconda3\envs\ibase\python.exe
#>
[CmdletBinding()]
param(
    [string]$Python = "C:\miniconda3\envs\ibase\python.exe"
)

$ErrorActionPreference = "Stop"

$packagingDir = $PSScriptRoot
$root = Split-Path -Parent $packagingDir
$spec = Join-Path $packagingDir "zpyaudio.spec"
$versionFile = Join-Path $packagingDir "version_info.txt"
$readme = Join-Path $packagingDir "PORTABLE-README.txt"

if (-not (Test-Path $Python)) { throw "找不到 Python 解释器：$Python" }

$version = (& $Python -c "import sys; sys.path.insert(0, 'src'); from zpyaudio import __version__; print(__version__)").Trim()
if ($LASTEXITCODE -ne 0) { throw "读取版本号失败" }
$dist = Join-Path $root "dist/zpyaudio-${version}-win.amd64-portable"

Write-Host "== 打包 zpyaudio $version ==" -ForegroundColor Cyan
Write-Host "   解释器：$Python"
Write-Host "   规格文件：$spec"
Write-Host "   产物目录：$dist"
Write-Host "   版本资源：$versionFile"

Push-Location $root
try {
    & $Python -m PyInstaller --clean --noconfirm --distpath (Join-Path $root "dist") --workpath (Join-Path $root "build/pyinstaller") $spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller 失败（exit $LASTEXITCODE）" }
} finally {
    Pop-Location
}

if (-not (Test-Path $dist)) { throw "未生成产物目录：$dist" }

Copy-Item -Path $readme -Destination (Join-Path $dist "PORTABLE-README.txt") -Force

$exe = Join-Path $dist "zpyaudio.exe"
if (-not (Test-Path $exe)) { throw "产物缺少 zpyaudio.exe：$exe" }

# ffmpeg 必须**不在**包内（它由用户自行安装，程序启动时自动检测）
$foundTools = @()
$foundTools += Get-ChildItem -Path $dist -Recurse -Filter "ffmpeg*.exe" -ErrorAction SilentlyContinue
$foundTools += Get-ChildItem -Path $dist -Recurse -Filter "ffprobe*.exe" -ErrorAction SilentlyContinue

$size = (Get-ChildItem -Path $dist -Recurse -File | Measure-Object -Property Length -Sum).Sum
$fileCount = (Get-ChildItem -Path $dist -Recurse -File | Measure-Object).Count

Write-Host ""
Write-Host "== 打包完成 ==" -ForegroundColor Green
Write-Host ("   产物目录：{0}" -f $dist)
Write-Host ("   体积：{0:N1} MB（{1} 个文件）" -f ($size / 1MB), $fileCount)
Write-Host ("   可执行程序：{0}" -f $exe)
Write-Host ("   版本资源：{0}" -f (Get-Item $exe).VersionInfo.FileVersion)
if ($foundTools.Count -gt 0) {
    $names = ($foundTools | ForEach-Object { $_.Name }) -join ", "
    Write-Warning "产物中发现了 ffmpeg/ffprobe（本版本约定不打包 ffmpeg）：$names"
} else {
    Write-Host "   ffmpeg：未打包（符合约定，运行时自动检测用户环境）" -ForegroundColor Green
}

exit 0
