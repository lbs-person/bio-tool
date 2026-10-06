# 把「文档」和「桌面」重定向到 D 盘
#
# 目的：这两处共 3.3 GB（文档 2.1 GB + 桌面 1.2 GB），而 C 盘只剩 8.7 GB。
# 搬到 D 盘后 C 盘能多出约 3.3 GB。
#
# 做法说明（为什么是这个顺序）：
#   1. 先用 robocopy /MOVE 把内容搬过去（复制校验成功才删源，可中断续跑）
#   2. 再改 HKCU 的 User Shell Folders 指向新位置
#   3. 重启资源管理器让改动生效
# 反过来做（先改注册表）会让资源管理器立刻指向空目录，看起来像文件丢了。
#
# 这个操作不改变程序的访问方式：程序仍通过「文档」这个逻辑名访问，
# 由 Windows 负责映射到 D 盘。
#
# 用法：普通权限即可，不需要管理员。
#
# 注意：本文件必须以 UTF-8 **带 BOM** 保存。Windows PowerShell 5.1 读 .ps1
# 时默认按 ANSI 解析，无 BOM 的中文会变乱码并导致语法错误。

$ErrorActionPreference = 'Stop'

# 刻意不用哈希表存这些值。Windows PowerShell 5.1 里
# `$pairs | Measure-Object -Property Size` 不会把哈希表的键当属性读取，
# 会报 "The property Size cannot be found"。用平行数组最稳。
$names   = @('Documents', 'Desktop')
$labels  = @('文档', '桌面')
$regKeys = @('Personal', 'Desktop')

$destRoot = 'D:\Users\lbs'
$srcRoot  = $env:USERPROFILE

Write-Host "=================================================================="
Write-Host "把文档与桌面重定向到 D 盘"
Write-Host "=================================================================="
Write-Host ""

# ---- 先看清楚要搬什么，并算总量 ----
$total = 0
for ($i = 0; $i -lt $names.Count; $i++) {
    $src = Join-Path $srcRoot $names[$i]
    if (-not (Test-Path $src)) { continue }
    $sz = (Get-ChildItem $src -Recurse -File -ErrorAction SilentlyContinue |
           Measure-Object -Property Length -Sum).Sum
    if (-not $sz) { $sz = 0 }
    $total += $sz
    Write-Host ("  {0,-4} {1,10:N0} MB   {2}" -f $labels[$i], ($sz / 1MB), $src)
    Write-Host ("       -> {0}" -f (Join-Path $destRoot $names[$i]))
}

Write-Host ""
Write-Host ("合计 {0:N0} MB 将从 C 盘移到 D 盘。" -f ($total / 1MB))

$d = Get-PSDrive D
if ($d.Free -lt $total * 1.15) {
    Write-Host ("D 盘空间不足：需要约 {0:N0} MB，只有 {1:N0} MB。" -f ($total / 1MB), ($d.Free / 1MB)) -ForegroundColor Red
    Read-Host "按回车退出"
    exit 1
}
Write-Host ("D 盘可用 {0:N0} MB，充足。" -f ($d.Free / 1MB))
Write-Host ""

$ans = Read-Host "确认执行？输入 yes 继续，其它取消"
if ($ans -ne 'yes') {
    Write-Host "已取消，未做任何改动。" -ForegroundColor Yellow
    Read-Host "按回车退出"
    exit 0
}

New-Item -ItemType Directory -Path $destRoot -Force | Out-Null

for ($i = 0; $i -lt $names.Count; $i++) {
    $src = Join-Path $srcRoot $names[$i]
    if (-not (Test-Path $src)) { continue }
    $dst = Join-Path $destRoot $names[$i]

    Write-Host ""
    Write-Host ("[*] 搬迁 {0}" -f $labels[$i]) -ForegroundColor Cyan
    New-Item -ItemType Directory -Path $dst -Force | Out-Null

    # /MOVE 复制校验成功后删源；/XJ 跳过联接点，避免无限递归
    $null = robocopy $src $dst /E /MOVE /XJ /R:1 /W:1 /NFL /NDL /NJH /NJS
    $rc = $LASTEXITCODE
    if ($rc -ge 8) {
        Write-Host ("    robocopy 失败，退出码 {0}，跳过改注册表。" -f $rc) -ForegroundColor Red
        continue
    }
    Write-Host ("    robocopy 退出码 {0}（0-7 为成功）" -f $rc)

    # 源目录被 /MOVE 删掉了，重建一个空壳（部分程序会假设它存在）
    if (-not (Test-Path $src)) {
        New-Item -ItemType Directory -Path $src -Force | Out-Null
    }

    # ---- 改注册表指向 ----
    $regPath = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders'
    $old = (Get-ItemProperty -Path $regPath -Name $regKeys[$i] -ErrorAction SilentlyContinue).($regKeys[$i])
    Set-ItemProperty -Path $regPath -Name $regKeys[$i] -Value $dst
    Write-Host ("    注册表 {0}: {1}" -f $regKeys[$i], $old)
    Write-Host ("              -> {0}" -f $dst)

    $regPath2 = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders'
    if (Get-ItemProperty -Path $regPath2 -Name $regKeys[$i] -ErrorAction SilentlyContinue) {
        Set-ItemProperty -Path $regPath2 -Name $regKeys[$i] -Value $dst
    }
    Write-Host "    完成" -ForegroundColor Green
}

# ---- 让资源管理器重新读取 ----
Write-Host ""
Write-Host "重启资源管理器以生效 …"
Stop-Process -Name explorer -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3
if (-not (Get-Process explorer -ErrorAction SilentlyContinue)) {
    Start-Process explorer
}

Write-Host ""
$c = Get-PSDrive C
Write-Host ("C 盘可用: {0:N2} GB" -f ($c.Free / 1GB)) -ForegroundColor Cyan
Write-Host ""
Write-Host "生效检查：" -ForegroundColor Yellow
Write-Host ("  桌面图标应仍在（实际存于 {0}\Desktop）" -f $destRoot)
Write-Host "  若个别程序仍指向旧路径，注销再登录一次即可。"
Read-Host "按回车退出"
