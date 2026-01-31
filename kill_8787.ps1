$ErrorActionPreference='SilentlyContinue'
$Port = 8787
$conns = Get-NetTCPConnection -LocalPort $Port -State Listen
if(-not $conns){
  Write-Host "✅ No listener on port $Port" -ForegroundColor Green
  exit 0
}
$pids = $conns.OwningProcess | Select-Object -Unique
foreach($id in $pids){
  $p = Get-Process -Id $id -ErrorAction SilentlyContinue
  if($p){
    Write-Host ("Killing PID {0} ({1}) on port {2}..." -f $id, $p.ProcessName, $Port) -ForegroundColor Yellow
    Stop-Process -Id $id -Force
    Write-Host ("✅ Killed PID {0}" -f $id) -ForegroundColor Green
  }
}