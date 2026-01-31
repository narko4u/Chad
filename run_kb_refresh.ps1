#requires -Version 7.0
$ErrorActionPreference="Stop"
Set-StrictMode -Version Latest

$Root = "C:\VaultSentinel\EmpireLabs\ChatBot"
$Py   = Join-Path $Root ".venv\Scripts\python.exe"
if(!(Test-Path $Py)){ throw "Missing venv python: $Py" }

# Set these as needed
$env:OLLAMA_BASE_URL   = "http://127.0.0.1:11434"
$env:EMBED_MODEL       = "nomic-embed-text"
$env:KB_SCRAPE_BASE    = "https://empirelabs.com.au"
$env:KB_SCRAPE_MAX_PAGES = "60"

Set-Location $Root

Write-Host "1) Scraping site -> kb\\scraped ..." -ForegroundColor Cyan
& $Py site_scrape.py

Write-Host "2) Ingesting kb -> rag_db ..." -ForegroundColor Cyan
& $Py rag_ingest.py

Write-Host "✅ KB refresh complete." -ForegroundColor Green
