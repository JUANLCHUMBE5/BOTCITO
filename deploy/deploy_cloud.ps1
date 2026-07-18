param(
    [Parameter(Mandatory=$false)]
    [string]$ServerHost = "",

    [string]$ServerUser = "ubuntu",

    [string]$KeyPath = "",

    [string]$RemoteBotDir = "/home/ubuntu/xrp-trading-bot",

    [switch]$IncludeState,

    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

# ── Validaciones ──────────────────────────────────────────────────
if (-not $ServerHost) {
    Write-Host "Uso: .\deploy_cloud.ps1 -ServerHost <IP> [-ServerUser ubuntu] [-KeyPath ~/.ssh/id_rsa] [-IncludeState] [-DryRun]" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Parametros:"
    Write-Host "  -ServerHost       IP o hostname del VPS (obligatorio)"
    Write-Host "  -ServerUser       Usuario SSH (default: ubuntu)"
    Write-Host "  -KeyPath          Ruta a la clave SSH privada"
    Write-Host "  -RemoteBotDir     Directorio remoto (default: /home/ubuntu/xrp-trading-bot)"
    Write-Host "  -IncludeState     Incluir .env, data/ y logs/ en el bundle"
    Write-Host "  -DryRun           Solo crear el bundle sin subir al servidor"
    exit 1
}

$sshArgs = @("-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=15")
if ($KeyPath) {
    $sshArgs += @("-i", $KeyPath)
}
$sshTarget = "${ServerUser}@${ServerHost}"

# ── Paso 1: Crear bundle ─────────────────────────────────────────
Write-Host "`n[1/5] Creando bundle de release..." -ForegroundColor Cyan
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$bundleName = "xrp-bot-deploy-$stamp"

if ($IncludeState) {
    $null = & (Join-Path $PSScriptRoot "package_release.ps1") -ProjectRoot $ProjectRoot -BundleName $bundleName -IncludeState
} else {
    $null = & (Join-Path $PSScriptRoot "package_release.ps1") -ProjectRoot $ProjectRoot -BundleName $bundleName
}

$bundlePath = Join-Path $ProjectRoot "dist\$bundleName.zip"
if (-not (Test-Path $bundlePath)) {
    Write-Host "ERROR: No se encontro el bundle en $bundlePath" -ForegroundColor Red
    exit 1
}

$bundleSizeMB = [math]::Round((Get-Item $bundlePath).Length / 1MB, 2)
Write-Host "Bundle creado: $bundlePath ($bundleSizeMB MB)" -ForegroundColor Green

if ($DryRun) {
    Write-Host "`n[DRY-RUN] Bundle listo. No se subira al servidor." -ForegroundColor Yellow
    Write-Host "Para subir manualmente:"
    Write-Host "  scp $($sshArgs -join ' ') `"$bundlePath`" ${sshTarget}:/tmp/"
    Write-Host "  ssh $($sshArgs -join ' ') ${sshTarget} 'cd /tmp && unzip -o $bundleName.zip && cd payload && chmod +x deploy/install_bundle.sh && sudo ./deploy/install_bundle.sh'"
    exit 0
}

# ── Paso 2: Subir bundle al servidor ─────────────────────────────
Write-Host "`n[2/5] Subiendo bundle al servidor ${ServerHost}..." -ForegroundColor Cyan
if ($KeyPath) {
    scp -i $KeyPath -o StrictHostKeyChecking=no -o ConnectTimeout=15 $bundlePath "${sshTarget}:/tmp/$bundleName.zip"
} else {
    scp -o StrictHostKeyChecking=no -o ConnectTimeout=15 $bundlePath "${sshTarget}:/tmp/$bundleName.zip"
}
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: No se pudo subir el bundle al servidor" -ForegroundColor Red
    exit 1
}
Write-Host "Bundle subido exitosamente" -ForegroundColor Green

# ── Paso 3: Descomprimir en el servidor ──────────────────────────
Write-Host "`n[3/5] Descomprimiendo e instalando en el servidor..." -ForegroundColor Cyan
$remoteCommands = @"
set -e
cd /tmp
rm -rf $bundleName
set +e
unzip -o $bundleName.zip -d $bundleName
set -e
cd $bundleName/payload
sed -i 's/\r$//' deploy/install_bundle.sh
chmod +x deploy/install_bundle.sh
sudo BOT_DIR=$RemoteBotDir BOT_USER=$ServerUser ./deploy/install_bundle.sh
rm -rf /tmp/$bundleName /tmp/$bundleName.zip
"@

if ($KeyPath) {
    ssh -i $KeyPath -o StrictHostKeyChecking=no -o ConnectTimeout=15 $sshTarget $remoteCommands
} else {
    ssh -o StrictHostKeyChecking=no -o ConnectTimeout=15 $sshTarget $remoteCommands
}
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: La instalacion fallo en el servidor" -ForegroundColor Red
    exit 1
}
Write-Host "Instalacion completada" -ForegroundColor Green

# ── Paso 4: Reiniciar servicios ──────────────────────────────────
Write-Host "`n[4/5] Reiniciando servicios systemd..." -ForegroundColor Cyan
$restartCommands = @"
sudo systemctl daemon-reload
sudo systemctl restart xrp-bot xrp-watchdog
sleep 3
echo '--- Estado xrp-bot ---'
sudo systemctl status xrp-bot --no-pager -l 2>&1 | head -20
echo '--- Estado xrp-watchdog ---'
sudo systemctl status xrp-watchdog --no-pager -l 2>&1 | head -10
"@

if ($KeyPath) {
    ssh -i $KeyPath -o StrictHostKeyChecking=no -o ConnectTimeout=15 $sshTarget $restartCommands
} else {
    ssh -o StrictHostKeyChecking=no -o ConnectTimeout=15 $sshTarget $restartCommands
}

# ── Paso 5: Health check ─────────────────────────────────────────
Write-Host "`n[5/5] Verificando health check (15 segundos)..." -ForegroundColor Cyan
Start-Sleep -Seconds 15

$healthCommands = @"
if systemctl is-active --quiet xrp-bot; then
    echo 'OK: xrp-bot esta activo'
    tail -5 $RemoteBotDir/logs/bot.log 2>/dev/null || echo '(sin logs aun)'
else
    echo 'ALERTA: xrp-bot NO esta activo'
    sudo journalctl -u xrp-bot --no-pager -n 20
    exit 1
fi
"@

if ($KeyPath) {
    ssh -i $KeyPath -o StrictHostKeyChecking=no -o ConnectTimeout=15 $sshTarget $healthCommands
} else {
    ssh -o StrictHostKeyChecking=no -o ConnectTimeout=15 $sshTarget $healthCommands
}
if ($LASTEXITCODE -ne 0) {
    Write-Host "`nALERTA: El bot no arranco correctamente. Revisa los logs." -ForegroundColor Red
    exit 1
}

Write-Host "`n=====================================" -ForegroundColor Green
Write-Host "Deploy completado exitosamente!" -ForegroundColor Green
Write-Host "Servidor: $ServerHost" -ForegroundColor Green
Write-Host "Directorio: $RemoteBotDir" -ForegroundColor Green
Write-Host "Bundle: $bundleName" -ForegroundColor Green
Write-Host "=====================================" -ForegroundColor Green
Write-Host ""
Write-Host "Comandos utiles:" -ForegroundColor Yellow
Write-Host "  ssh $($sshArgs -join ' ') ${sshTarget} 'sudo journalctl -u xrp-bot -f'"
Write-Host "  ssh $($sshArgs -join ' ') ${sshTarget} 'tail -f $RemoteBotDir/logs/bot.log'"
Write-Host "  ssh $($sshArgs -join ' ') ${sshTarget} 'sudo systemctl status xrp-bot'"
