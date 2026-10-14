<#
.SYNOPSIS
  Validação do dvinv no Windows (VDI) antes de taguear uma versão. Só leitura no Dataverse.

.DESCRIPTION
  Etapas (cada uma imprime OK/FALHOU; o script para na primeira falha):
    1. Python >= 3.10, dependências e testes offline (pipeline + regressão P0)
    2. (com -Config) check de acesso ao ambiente
    3. (com -Config -Extrair) extract + render completos
    4. (com -Config) render duas vezes seguidas: a pasta do vault é trocada atomicamente e, com o vault em Git,
       a segunda renderização não pode gerar diferença (CRLF, ordem, data)
    5. (com -Config) consultas sobre o vault (onde-usa/buscar) sem inventory.yaml

.EXAMPLE
  .\validar-windows.ps1                                    # só testes offline
  .\validar-windows.ps1 -Config C:\kb\cliente\inventory.test.yaml           # usa o último snapshot
  .\validar-windows.ps1 -Config C:\kb\cliente\inventory.test.yaml -Extrair  # extrai de novo (demora)
#>
param(
  [string]$Config,
  [switch]$Extrair,
  [string]$Python = "python"
)
$ErrorActionPreference = "Stop"
$Skill = Split-Path -Parent $PSScriptRoot
$Dvinv = Join-Path $PSScriptRoot "dvinv.py"

function Step($nome, [scriptblock]$bloco) {
  Write-Host "`n== $nome" -ForegroundColor Cyan
  try { & $bloco; Write-Host "OK   $nome" -ForegroundColor Green }
  catch { Write-Host "FALHOU $nome :: $($_.Exception.Message)" -ForegroundColor Red; exit 1 }
}
function Run([string[]]$argv) {
  & $Python @argv
  if ($LASTEXITCODE -ne 0) { throw "saída $LASTEXITCODE em: $($argv -join ' ')" }
}

Step "Python e dependências" {
  $v = & $Python -c "import sys; print('%d.%d' % sys.version_info[:2])"
  if ([version]$v -lt [version]"3.10") { throw "Python $v — precisa de 3.10+" }
  Run @("-m", "pip", "install", "-q", "-r", (Join-Path $PSScriptRoot "requirements.txt"))
}
Step "Teste offline: pipeline completo (inclui render determinístico)" {
  Run @((Join-Path $Skill "tests\test_pipeline.py"))
}
Step "Teste offline: regressão P0" {
  Run @((Join-Path $Skill "tests\test_regressao_p0.py"))
}
if (-not $Config) { Write-Host "`nSem -Config: validação offline concluída." -ForegroundColor Green; exit 0 }

Step "check (acesso ao ambiente, só leitura)" { Run @($Dvinv, "check", "-c", $Config) }
if ($Extrair) { Step "extract + render (completo)" { Run @($Dvinv, "all", "-c", $Config) } }

$vault = & $Python -c "import sys; sys.path.insert(0, r'$PSScriptRoot'); from dvinv import config; c = config.load(r'$Config'); print(c.vault_dir); print(c.vault_root)"
$vaultDir, $vaultRoot = $vault[0], $vault[1]

Step "render 1 (troca atômica da pasta do vault)" { Run @($Dvinv, "render", "-c", $Config) }
Step "render 2 sem mudança no ambiente = mesmos arquivos" {
  $antes = Get-ChildItem -Recurse -File $vaultDir | Where-Object { $_.FullName -notmatch '\\\.' } |
           Get-FileHash -Algorithm SHA256 | ForEach-Object { "$($_.Hash) $($_.Path)" }
  Run @($Dvinv, "render", "-c", $Config)
  $depois = Get-ChildItem -Recurse -File $vaultDir | Where-Object { $_.FullName -notmatch '\\\.' } |
            Get-FileHash -Algorithm SHA256 | ForEach-Object { "$($_.Hash) $($_.Path)" }
  $diff = Compare-Object $antes $depois
  if ($diff) { $diff | Select-Object -First 10 | Out-String | Write-Host; throw "$($diff.Count) diferença(s) entre renders" }
  $crlf = Get-ChildItem -Recurse -File $vaultDir -Include *.md, *.csv | Where-Object { (Get-Content -Raw $_.FullName) -match "`r`n" }
  if ($crlf) { throw "$($crlf.Count) arquivo(s) com CRLF (ex. $($crlf[0].Name))" }
}
Step "consultas sobre o vault sem inventory.yaml" {
  Run @($Dvinv, "buscar", "account", "--vault", $vaultDir)
  Run @($Dvinv, "onde-usa", "account", "--vault", $vaultDir, "--limite", "5")
}
if (Test-Path (Join-Path $vaultRoot ".git")) {
  Step "git: caminhos longos e status" {
    $lp = git -C $vaultRoot config core.longpaths
    if ($lp -ne "true") { Write-Host "aviso: git config core.longpaths true recomendado neste repositório" -ForegroundColor Yellow }
    git -C $vaultRoot status --short | Select-Object -First 5
  }
}
Write-Host "`nValidação Windows concluída. Registre a versão do Windows, do Python e o resultado no CHANGELOG antes da tag." -ForegroundColor Green
