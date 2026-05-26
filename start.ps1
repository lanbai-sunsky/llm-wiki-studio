$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$BundledPython = 'C:\Users\28634\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
if (Test-Path $BundledPython) {
  $Python = $BundledPython
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
  $Python = 'py'
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
  $Python = 'python'
} else {
  throw 'Python was not found. Install Python 3.11+ or run from the Codex bundled runtime.'
}

Write-Host '正在启动 LLM Wiki Studio...'
& $Python -c "import langgraph, langchain, langchain_openai" 2>$null
if ($LASTEXITCODE -ne 0) {
  Write-Host '正在安装 LangChain / LangGraph 依赖...'
  & $Python -m pip install -r requirements.txt
}
Write-Host '请打开 http://127.0.0.1:8877'
& $Python server.py 8877
