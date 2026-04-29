#!/usr/bin/env pwsh
# 로컬 dev 백엔드 기동 — .env.local 자동 로드 + uvicorn 대신 main_v2 직접 실행.
# EC2 배포와 완전히 분리: EC2 는 systemd + /opt/qa-pipeline/.env 사용.

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

# --- 기본값 (env 없어도 기동 가능하게) ---
$env:PYTHONIOENCODING = "utf-8"
$env:AWS_REGION = "us-east-1"
$env:PORT = "8081"

# --- .env.local 파싱 → $env 주입 ---
$envFile = Join-Path $scriptDir ".env.local"
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -eq "" -or $line.StartsWith("#")) { return }
        $kv = $line -split "=", 2
        if ($kv.Count -eq 2) {
            $name = $kv[0].Trim()
            $value = $kv[1].Trim()
            # 끝에 따옴표 있으면 제거
            if ($value.StartsWith('"') -and $value.EndsWith('"')) {
                $value = $value.Substring(1, $value.Length - 2)
            }
            Set-Item -Path "Env:$name" -Value $value
        }
    }
    Write-Host "[launch] .env.local 로드 완료 — QA_GT_XLSX_PATH=$env:QA_GT_XLSX_PATH"
} else {
    Write-Host "[launch] .env.local 없음 — 기본 환경변수만 사용 (GT 는 Desktop 자동 탐색)"
}

# --- 서버 기동 ---
Write-Host "[launch] 백엔드 기동 — port=$env:PORT"
& "C:\Users\META M\.conda\envs\py313\python.exe" -m v2.serving.main_v2 *>> "server_v2_stdout.log" 2>&1
