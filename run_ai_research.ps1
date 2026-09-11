$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "========================================"
Write-Host "  2C AI Radar - Local AI Research"
Write-Host "========================================"
Write-Host ""

# Always run from this repository directory
Set-Location $PSScriptRoot


# ------------------------------------------------------------
# 1. Check required environment variables
# ------------------------------------------------------------

if (-not $env:LITELLM_API_KEY) {
    Write-Host "[ERROR] LITELLM_API_KEY is not configured."
    Write-Host ""
    Write-Host "Please add it to your Windows User Environment Variables."
    exit 1
}

if (-not $env:LITELLM_BASE_URL) {
    Write-Host "[ERROR] LITELLM_BASE_URL is not configured."
    Write-Host ""
    Write-Host "Please add it to your Windows User Environment Variables."
    exit 1
}


if (-not $env:LITELLM_MODEL) {
    $env:LITELLM_MODEL = "gpt-5.6-luna"
}


if (-not $env:MAX_LLM_CALLS) {
    $env:MAX_LLM_CALLS = "50"
}


$env:INPUT_PRICE_RMB_PER_M = "1.35"
$env:OUTPUT_PRICE_RMB_PER_M = "8.11"
$env:PYTHONUTF8 = "1"


Write-Host "[OK] Model: $env:LITELLM_MODEL"
Write-Host "[OK] Max new LLM calls: $env:MAX_LLM_CALLS"
Write-Host ""


# ------------------------------------------------------------
# 2. Check company LiteLLM connectivity
# ------------------------------------------------------------

Write-Host "[1/7] Checking company LiteLLM connectivity..."

$baseUrl = $env:LITELLM_BASE_URL.TrimEnd("/")
$modelsUrl = "$baseUrl/models"

try {

    $statusCode = curl.exe `
        -sS `
        -o NUL `
        -w "%{http_code}" `
        $modelsUrl `
        -H "Authorization: Bearer $env:LITELLM_API_KEY"

    Write-Host "HTTP status: $statusCode"

    if ($statusCode -eq "000") {
        throw "Cannot reach LiteLLM gateway."
    }

}
catch {

    Write-Host ""
    Write-Host "[ERROR] Cannot access company LiteLLM."
    Write-Host "Make sure you are connected to company network or VPN."
    Write-Host ""
    exit 1
}


Write-Host "[OK] LiteLLM gateway is reachable."
Write-Host ""


# ------------------------------------------------------------
# 3. Pull latest Product Hunt data
# ------------------------------------------------------------

Write-Host "[2/7] Pulling latest GitHub data..."

git pull --rebase origin main

if ($LASTEXITCODE -ne 0) {
    throw "git pull failed"
}

Write-Host ""


# ------------------------------------------------------------
# 4. Install/update Python dependencies
# ------------------------------------------------------------

Write-Host "[3/7] Checking Python dependencies..."

python -m pip install -r requirements.txt

if ($LASTEXITCODE -ne 0) {
    throw "pip install failed"
}

Write-Host ""


# ------------------------------------------------------------
# 5. Run Luna classification + lightweight research
# ------------------------------------------------------------

Write-Host "[4/7] Running AI classification and research..."
Write-Host ""

python scripts/classify_products.py

if ($LASTEXITCODE -ne 0) {
    throw "classify_products.py failed"
}

Write-Host ""


# ------------------------------------------------------------
# 6. Build dashboard data
# ------------------------------------------------------------

Write-Host "[5/7] Building dashboard data..."

python scripts/build_dashboard_data.py

if ($LASTEXITCODE -ne 0) {
    throw "build_dashboard_data.py failed"
}

Write-Host ""


# ------------------------------------------------------------
# 7. Commit results
# ------------------------------------------------------------

Write-Host "[6/7] Saving research results..."

git add data/

$changes = git status --porcelain

if (-not $changes) {

    Write-Host ""
    Write-Host "No new research results to commit."
    Write-Host ""

}
else {

    git commit -m "Update local AI product research"

    if ($LASTEXITCODE -ne 0) {
        throw "git commit failed"
    }


    Write-Host ""
    Write-Host "[7/7] Syncing with GitHub..."

    git pull --rebase origin main

    if ($LASTEXITCODE -ne 0) {
        throw "git pull --rebase failed"
    }


    git push origin main

    if ($LASTEXITCODE -ne 0) {
        throw "git push failed"
    }

}


Write-Host ""
Write-Host "========================================"
Write-Host "  AI research completed successfully."
Write-Host "========================================"
Write-Host ""

Read-Host "Press Enter to close"
