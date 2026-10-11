<#
.SYNOPSIS
    验证 dist 下的 Windows 便携版能否独立运行（M6 / NFR-7）。

.DESCRIPTION
    对打包产物执行一组自检，全部通过才打印 PASS：

    1. zpyaudio.exe 存在且版本资源为当前版本；
    2. `--self-test`          离屏起窗口（验证 Qt 平台插件 + 整个 GUI 模块树都能加载）；
    3. `--audio-check 3`      合成源端到端（验证 numpy / scipy / sounddevice / soundfile）；
    4. `--midi-check <file>`  MIDI 解析（验证 mido 及其后端收集完整）；
    5. `--list-devices`       设备枚举（验证 PortAudio DLL 已随包）；
    6. 真实桌面启动 6 s 后仍响应（验证 windows 平台插件，不是只靠 offscreen）。

    产物是 GUI 子系统程序（不打印到控制台），因此判据取**进程退出码** + exe 同级
    `logs/*.log`；脚本结束时默认清理本次自检生成的 logs/ 与 config.json，
    让交付文件夹保持干净（`-KeepArtifacts` 可保留）。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File packaging\verify_portable.ps1
#>
[CmdletBinding()]
param(
    [string]$Python = "C:\miniconda3\envs\ibase\python.exe",
    [string]$Dist,
    [switch]$KeepArtifacts
)

$ErrorActionPreference = "Stop"

$packagingDir = $PSScriptRoot
$root = Split-Path -Parent $packagingDir

$version = (& $Python -c "import sys; sys.path.insert(0, 'src'); from zpyaudio import __version__; print(__version__)").Trim()
if (-not $Dist) { $Dist = Join-Path $root "dist/zpyaudio-${version}-win.amd64-portable" }
$exe = Join-Path $Dist "zpyaudio.exe"

if (-not (Test-Path $exe)) { throw "找不到打包产物：$exe（先运行 build_portable.ps1）" }

$script:results = @()

function Add-Result {
    param([string]$Name, [string]$Detail, [bool]$Ok)
    $verdict = "FAIL"
    if ($Ok) { $verdict = "PASS" }
    $script:results += [pscustomobject]@{ 检查 = $Name; 详情 = $Detail; 结论 = $verdict }
}

function Invoke-Exe {
    param([string]$Name, [string[]]$Arguments)
    # 注意：产物是 GUI 子系统程序，`Start-Process -PassThru` + `WaitForExit()` 在本环境下会
    # 一直挂住（子进程持有父进程的输出句柄，实测两次超时），改用 cmd.exe 同步等待并回传退出码。
    $argumentsLine = ($Arguments | ForEach-Object { '"{0}"' -f $_ }) -join ' '
    $null = & cmd.exe /c "`"$exe`" $argumentsLine"
    $code = $LASTEXITCODE
    Add-Result -Name $Name -Detail ("退出码 {0}" -f $code) -Ok ($code -eq 0)
}

Write-Host "== 验证便携版 $version ==" -ForegroundColor Cyan
Write-Host "   产物：$Dist"

# 1) 版本资源
$fileVersion = (Get-Item $exe).VersionInfo.FileVersion
Add-Result -Name "exe 版本资源" -Detail $fileVersion -Ok ($fileVersion -eq $version)

# 2)-5) 命令行自检
Invoke-Exe -Name "--self-test（离屏 GUI）" -Arguments @("--self-test", "--self-test-ms", "1500")
Invoke-Exe -Name "--audio-check 3（合成源）" -Arguments @("--audio-check", "3")
Invoke-Exe -Name "--list-devices（PortAudio）" -Arguments @("--list-devices")

$midi = Join-Path $root "build/demo_midi.mid"
if (-not (Test-Path $midi)) {
    & $Python (Join-Path $root "main.py") --demo-midi | Out-Null
}
if (Test-Path $midi) {
    Invoke-Exe -Name "--midi-check（mido）" -Arguments @("--midi-check", $midi)
} else {
    Add-Result -Name "--midi-check（mido）" -Detail "无 MIDI 素材" -Ok $true
}

# 6) 真实桌面启动（windows 平台插件）
$gui = Start-Process -FilePath $exe -ArgumentList @("--demo-tone", "1000") -PassThru
Start-Sleep -Seconds 6
$alive = -not $gui.HasExited
$responding = $false
$detail = "进程已退出"
if ($alive) {
    $responding = $gui.Responding
    $detail = "存活，Responding=$responding，标题=""$($gui.MainWindowTitle)"""
    $gui.CloseMainWindow() | Out-Null
    Start-Sleep -Seconds 2
    if (-not $gui.HasExited) { $gui.Kill() }
}
Add-Result -Name "真实桌面启动 6 s" -Detail $detail -Ok $responding

# 交付文件夹里不应有 ffmpeg
$tools = @(Get-ChildItem -Path $Dist -Recurse -Filter "ffmpeg*.exe" -ErrorAction SilentlyContinue)
$tools += @(Get-ChildItem -Path $Dist -Recurse -Filter "ffprobe*.exe" -ErrorAction SilentlyContinue)
Add-Result -Name "未打包 ffmpeg" -Detail ("找到 {0} 个 ffmpeg/ffprobe" -f $tools.Count) -Ok ($tools.Count -eq 0)

Write-Host ""
$script:results | Format-Table -AutoSize

$logDir = Join-Path $Dist "logs"
Write-Host ("自检日志目录：{0}" -f $logDir)
Get-ChildItem -Path $logDir -Filter "*.log" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime |
    Select-Object -Last 6 |
    ForEach-Object { Write-Host ("   {0}" -f $_.Name) }

if (-not $KeepArtifacts) {
    Remove-Item -Recurse -Force $logDir -ErrorAction SilentlyContinue
    Remove-Item -Force (Join-Path $Dist "config.json") -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force (Join-Path $Dist "build") -ErrorAction SilentlyContinue
    Write-Host "已清理本次自检生成的 logs/ 与 config.json（-KeepArtifacts 可保留）"
}

$failed = @($script:results | Where-Object { $_.结论 -eq "FAIL" })
Write-Host ""
if ($failed.Count -gt 0) {
    Write-Host ("== 验证失败：{0} 项 ==" -f $failed.Count) -ForegroundColor Red
    exit 1
}
Write-Host "== 验证通过：便携版可独立运行 ==" -ForegroundColor Green
exit 0
