# Builds inside this checkout; does not change VS Code or the running relay.
$ErrorActionPreference = 'Stop'
$csc = Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
$out = Join-Path (Split-Path $PSScriptRoot -Parent) 'bin'
New-Item -ItemType Directory -Force $out | Out-Null
$source = Join-Path $PSScriptRoot 'codex_shared.cs'
$outputFile = Join-Path $out 'codex-shared.exe'
& $csc /nologo /optimize /target:exe "/out:$outputFile" $source
if ($LASTEXITCODE -ne 0) { throw 'Wrapper compilation failed' }
Write-Output "Built $out/codex-shared.exe (not activated)"
