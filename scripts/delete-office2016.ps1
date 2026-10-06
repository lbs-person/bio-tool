# 删除 Office2016 残留目录（需要管理员权限）
#
# 背景：C:\Program Files\Office2016 占 1.03 GB，注册表里没有任何引用，
# 独立于已安装的 Office（后者在 C:\Program Files\Microsoft Office）。
# 它内含 KMS 相关文件（key\KMS、ActInst、ActCheck.cmd、pkeyconfig-office.xrm-ms）
# 与一套完整的 Office16 程序，属于第三方 Office 安装包/激活工具目录。
#
# 删除它不会卸载已装的 Office，也不影响 Office 的激活状态（激活信息记在
# 注册表和系统授权存储里）。但如果以后需要重新激活 Office，这个工具目录
# 就没了——所以脚本先把文件列表存盘，再删除。
#
# 用法：右键「以管理员身份运行 PowerShell」，然后执行本脚本。

$ErrorActionPreference = 'Continue'
$target = 'C:\Program Files\Office2016'

if (-not (Test-Path $target)) {
    Write-Host "目录不存在，无需处理：$target" -ForegroundColor Yellow
    Read-Host "按回车退出"
    exit 0
}

# 确认是管理员
$isAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    Write-Host "需要管理员权限。请右键「以管理员身份运行 PowerShell」后重试。" -ForegroundColor Red
    Read-Host "按回车退出"
    exit 1
}

$size = (Get-ChildItem $target -Recurse -File -ErrorAction SilentlyContinue |
         Measure-Object Length -Sum).Sum
Write-Host ("目标: {0}" -f $target)
Write-Host ("体积: {0:N0} MB" -f ($size / 1MB))
Write-Host ""

# 先把文件清单留档，万一以后需要复原或核对
$listFile = Join-Path $env:USERPROFILE "Office2016-文件清单.txt"
Get-ChildItem $target -Recurse -File -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty FullName | Set-Content $listFile -Encoding UTF8
Write-Host "文件清单已存: $listFile" -ForegroundColor Cyan
Write-Host ""

$ans = Read-Host "确认删除？输入 yes 继续，其它任意键取消"
if ($ans -ne 'yes') {
    Write-Host "已取消，未做任何改动。" -ForegroundColor Yellow
    Read-Host "按回车退出"
    exit 0
}

# 清掉只读属性，再删
Write-Host "清理只读属性 …"
Get-ChildItem $target -Recurse -File -Force -ErrorAction SilentlyContinue |
    ForEach-Object { try { $_.IsReadOnly = $false } catch {} }

Write-Host "删除中 …"
Remove-Item $target -Recurse -Force -ErrorAction SilentlyContinue

if (Test-Path $target) {
    $remain = (Get-ChildItem $target -Recurse -File -ErrorAction SilentlyContinue |
               Measure-Object Length -Sum).Sum
    Write-Host ("部分文件被占用，仍剩 {0:N0} MB。可重启后再执行一次。" -f ($remain / 1MB)) -ForegroundColor Yellow
} else {
    Write-Host "已删除。" -ForegroundColor Green
}

$c = Get-PSDrive C
Write-Host ("C 盘可用: {0:N2} GB" -f ($c.Free / 1GB)) -ForegroundColor Cyan
Read-Host "按回车退出"
