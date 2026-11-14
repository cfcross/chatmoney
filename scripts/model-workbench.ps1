param([ValidateSet("start","stop","status")] [string]$Action="start", [switch]$TrustCertificate, [switch]$OpenWorkbench)
$ErrorActionPreference = "Stop"
Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    $python = (Resolve-Path ".venv/Scripts/python.exe").Path
    $pg = ".runtime/postgres/bin/pg_ctl.exe"
    if ($Action -eq "start") {
        & $pg -D .runtime/pgdata status | Out-Null
        if ($LASTEXITCODE -ne 0) {
            & $pg -D .runtime/pgdata -l .runtime/postgres.log -o "-h 127.0.0.1 -p 55432" -w start
            if ($LASTEXITCODE -ne 0) { throw "PostgreSQL failed to start" }
        }
        . .runtime/env.ps1 dev
        $cert = & $python -m scripts.local_tls init
        if ($LASTEXITCODE -ne 0) { throw "Windows credential setup failed" }
        if ($TrustCertificate) {
            $public = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($cert)
            if (!(Test-Path ("Cert:/CurrentUser/Root/" + $public.Thumbprint))) {
                Import-Certificate -FilePath $cert -CertStoreLocation Cert:/CurrentUser/Root | Out-Null
            }
        }
        & $python -m scripts.bootstrap
        if ($LASTEXITCODE -ne 0) { throw "Migration failed" }
        & $python -m scripts.workspace_accounts
        if ($LASTEXITCODE -ne 0) { throw "Web account setup failed" }
        $env:MIGRATION_DATABASE_URL=$null
    }
    foreach ($item in @(@("api","uvicorn app.main:app","-m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-proxy-headers --no-access-log"),
                         @("worker","scripts.local_tls worker","-m scripts.local_tls worker"),
                         @("model-https","scripts.local_tls https","-m scripts.local_tls https"))) {
        $file = ".runtime/"+$item[0]+".pid"
        $proc = $null
        if (Test-Path -LiteralPath $file) {
            $number = [int](Get-Content -Raw -LiteralPath $file)
            $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$number"
            if ($proc -and ($proc.ExecutablePath -ne $python -or $proc.CommandLine -notlike ("*"+$item[1]+"*"))) {
                if ($item[0] -eq "worker" -and $proc.ExecutablePath -eq $python -and $proc.CommandLine -like "*-m app.worker*") {
                    if ($Action -ne "status") { throw "V1.0 worker active. Stop .runtime/live.ps1 before switching launchers." }
                } else {
                    if ($proc.ExecutablePath -ne $python) { $proc=$null }
                    else { throw "PID belongs to a different project command" }
                }
            }
        }
        if ($Action -eq "stop" -and $proc) {
            $children=Get-CimInstance Win32_Process -Filter ("ParentProcessId="+$proc.ProcessId) | Where-Object { $_.CommandLine -like ("*"+$item[1]+"*") }
            foreach($child in $children) { Stop-Process -Id $child.ProcessId }
            if (Get-Process -Id $proc.ProcessId -ErrorAction SilentlyContinue) { Stop-Process -Id $proc.ProcessId -ErrorAction SilentlyContinue }
            $proc=$null
        }
        if ($Action -eq "start" -and $proc -and $item[0] -eq "api" -and $proc.CommandLine -notlike "*--no-proxy-headers*") {
            throw "V1.0 API active. Stop .runtime/live.ps1 before switching launchers."
        }
        if ($Action -eq "start" -and !$proc) {
            $proc=Start-Process $python -ArgumentList $item[2] -WorkingDirectory $pwd -WindowStyle Hidden -PassThru -RedirectStandardOutput (".runtime/"+$item[0]+".out") -RedirectStandardError (".runtime/"+$item[0]+".err")
            Set-Content -LiteralPath $file -Value $proc.Id
        }
        if ($proc) { Write-Output ($item[0]+" running") } else { Write-Output ($item[0]+" stopped") }
    }
    if ($Action -eq "start" -and $OpenWorkbench) {
        $ready = $false
        for ($attempt = 0; $attempt -lt 12; $attempt++) {
            & $python -m scripts.local_tls verify *> $null
            if ($LASTEXITCODE -eq 0) { $ready = $true; break }
            Start-Sleep -Milliseconds 250
        }
        if (!$ready) { throw "The HTTPS workbench did not become ready. Check .runtime/model-https.err and the local certificate." }
        . .runtime/env.ps1 dev
        & $python -m scripts.workspace_accounts --open
        if ($LASTEXITCODE -ne 0) { throw "Could not open the workbench" }
    }
} finally { Pop-Location }
