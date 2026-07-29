# Cria um atalho "Music Aggregator" na Area de Trabalho.
# O atalho abre o app SEM janela de console (usa pythonw.exe do venv) e com o icone bonito.
# Rode:  clique direito > Executar com PowerShell   (ou:  powershell -ExecutionPolicy Bypass -File make_shortcut.ps1)

$ErrorActionPreference = "Stop"

# Pasta do projeto (onde este script esta)
$proj = $PSScriptRoot
if (-not $proj) { $proj = (Get-Location).Path }

$pythonw = Join-Path $proj ".venv\Scripts\pythonw.exe"
$mainpy  = Join-Path $proj "main.py"
$icon    = Join-Path $proj "assets\icon.ico"

# Verificacoes
if (-not (Test-Path $pythonw)) { throw "pythonw.exe nao encontrado em $pythonw. Crie o venv primeiro (py -m venv .venv)." }
if (-not (Test-Path $mainpy))  { throw "main.py nao encontrado em $mainpy." }
if (-not (Test-Path $icon))    { Write-Host "Aviso: icone nao encontrado, rode 'python make_icon.py' primeiro." -ForegroundColor Yellow }

$desktop  = [Environment]::GetFolderPath("Desktop")
$lnkPath  = Join-Path $desktop "Music Aggregator.lnk"

$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($lnkPath)
$lnk.TargetPath       = $pythonw
$lnk.Arguments        = '"' + $mainpy + '"'
$lnk.WorkingDirectory = $proj
$lnk.WindowStyle      = 1
$lnk.Description       = "Music Aggregator - Beatport, Bandcamp e Soulseek em um lugar so"
if (Test-Path $icon) { $lnk.IconLocation = $icon }
$lnk.Save()

Write-Host "Atalho criado na Area de Trabalho:" -ForegroundColor Green
Write-Host "  $lnkPath"
Write-Host "Pronto! De dois cliques no icone para abrir o app."
