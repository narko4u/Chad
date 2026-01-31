$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$env:OLLAMA_BASE_URL = 'http://127.0.0.1:11434'
$env:MODEL          = 'qwen2.5:7b-instruct'
$env:API_KEY        = ''
$env:CORS_ORIGINS   = '*'
$env:HOST           = '127.0.0.1'
$env:PORT           = '8787'
$env:CHAT_TEMP      = '0.4'
$env:NUM_CTX        = '4096'
$env:RAG_ENABLED    = '1'
$env:RAG_DB_PATH    = 'C:\VaultSentinel\EmpireLabs\ChatBot\rag_db'
$env:RAG_COLLECTION = 'empirelabs_kb'
$env:EMBED_MODEL    = 'nomic-embed-text'

Set-Location 'C:\VaultSentinel\EmpireLabs\ChatBot'
& 'C:\VaultSentinel\EmpireLabs\ChatBot\.venv\Scripts\python.exe' -m uvicorn server:app --host '127.0.0.1' --port 8787