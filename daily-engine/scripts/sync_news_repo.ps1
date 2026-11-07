param(
    [string]$TargetPath = "D:\Docker\Horizon-news",
    [string]$Remote = "https://github.com/xygx77/news.git",
    [switch]$Push
)

$ErrorActionPreference = "Stop"
$SourceRoot = Split-Path -Parent $PSScriptRoot

$isRepo = $false
if (Test-Path (Join-Path $TargetPath ".git")) {
    git -C $TargetPath rev-parse --is-inside-work-tree *> $null
    $isRepo = ($LASTEXITCODE -eq 0)
}

if (-not $isRepo) {
    if (Test-Path $TargetPath) {
        $extra = Get-ChildItem -Force $TargetPath | Where-Object { $_.Name -ne ".git" }
        if ($extra) {
            throw "Target path exists but is not a valid git repository: $TargetPath. Move it or use another -TargetPath."
        }
        Remove-Item -Recurse -Force $TargetPath
    }
    git clone $Remote $TargetPath
    if ($LASTEXITCODE -ne 0) {
        throw "git clone failed for $Remote (exit $LASTEXITCODE). Configure GitHub credentials and retry."
    }
} else {
    Push-Location $TargetPath
    git pull --ff-only
    $pullExit = $LASTEXITCODE
    Pop-Location
    if ($pullExit -ne 0) {
        throw "git pull failed in $TargetPath (exit $pullExit)."
    }
}

$Engine = Join-Path $TargetPath "daily-engine"
New-Item -ItemType Directory -Force -Path $Engine | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $TargetPath "docs\daily") | Out-Null

# Copy the self-contained engine. /MIR is scoped to daily-engine only.
$paths = @("src", "profiles", "templates", "tests", "scripts")
foreach ($relative in $paths) {
    $source = Join-Path $SourceRoot $relative
    if (Test-Path $source) {
        robocopy $source (Join-Path $Engine $relative) /MIR /NFL /NDL /NJH /NJS /NP | Out-Null
        if ($LASTEXITCODE -ge 8) {
            throw "robocopy failed with exit code $LASTEXITCODE for $relative"
        }
    }
}
foreach ($file in @("pyproject.toml", "uv.lock")) {
    Copy-Item (Join-Path $SourceRoot $file) (Join-Path $Engine $file) -Force
}

# This machine pins the Tsinghua mirror (UV_INDEX_URL / PIP_INDEX_URL), so a local
# `uv sync` rewrites the lock with mirror URLs. GitHub runners cannot use those
# URLs and `uv sync --locked` then fails before any page is generated, so rewrite
# them back to public PyPI before the lock is committed.
$lockPath = Join-Path $Engine "uv.lock"
$lockText = Get-Content $lockPath -Raw
if ($lockText -match 'pypi\.tuna\.tsinghua\.edu\.cn') {
    $portable = $lockText `
        -replace 'https://pypi\.tuna\.tsinghua\.edu\.cn/simple', 'https://pypi.org/simple' `
        -replace 'https://pypi\.tuna\.tsinghua\.edu\.cn/packages/', 'https://files.pythonhosted.org/packages/'
    [System.IO.File]::WriteAllText($lockPath, $portable, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "Rewrote uv.lock mirror URLs to public PyPI."
}

$dataDest = Join-Path $Engine "data"
New-Item -ItemType Directory -Force -Path $dataDest | Out-Null
foreach ($file in @("config.daily.json", "laser_keywords.json", "cn_market_holidays.json")) {
    Copy-Item (Join-Path $SourceRoot "data\$file") (Join-Path $dataDest $file) -Force
}

# Workflow template -> news repository
New-Item -ItemType Directory -Force -Path (Join-Path $TargetPath ".github\workflows") | Out-Null
Copy-Item (Join-Path $SourceRoot "scripts\daily.yml") (Join-Path $TargetPath ".github\workflows\daily.yml") -Force

# Site readme and the engine readme required by pyproject readme = "README.md"
Copy-Item (Join-Path $SourceRoot "README_DAILY.md") (Join-Path $TargetPath "README.md") -Force
Copy-Item (Join-Path $SourceRoot "README_DAILY.md") (Join-Path $Engine "README.md") -Force

# GitHub Pages: static files, no Jekyll processing
New-Item -ItemType File -Force -Path (Join-Path $TargetPath "docs\.nojekyll") | Out-Null

# Keep engine build artifacts out of git, but never ignore docs/daily
$ignorePath = Join-Path $TargetPath ".gitignore"
$entries = @("daily-engine/.venv/", "daily-engine/output/", "__pycache__/", "*.pyc")
$existing = if (Test-Path $ignorePath) { Get-Content $ignorePath } else { @() }
$missing = $entries | Where-Object { $existing -notcontains $_ }
if ($missing) {
    Add-Content $ignorePath ($missing -join "`n")
}

if ($Push) {
    Push-Location $TargetPath
    git add -A
    if ($LASTEXITCODE -ne 0) {
        throw "git add failed in $TargetPath (exit $LASTEXITCODE)."
    }
    git diff --cached --quiet
    if ($LASTEXITCODE -eq 0) {
        Write-Host "No changes to sync."
    } else {
        git commit -m "chore: sync daily engine"
        if ($LASTEXITCODE -ne 0) {
            throw "git commit failed in $TargetPath (exit $LASTEXITCODE)."
        }
        git pull --rebase --autostash
        if ($LASTEXITCODE -ne 0) {
            throw "git pull --rebase failed in $TargetPath (exit $LASTEXITCODE)."
        }
        git push
        if ($LASTEXITCODE -ne 0) {
            throw "git push failed (exit $LASTEXITCODE). Authenticate to GitHub and retry."
        }
    }
    Pop-Location
}

Write-Host "Synced daily engine to $TargetPath."
