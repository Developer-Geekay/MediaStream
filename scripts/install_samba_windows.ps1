# =============================================================================
# MediaStream — SMB2/3 enabler for Windows
# Enables the built-in Windows File Sharing (LanmanServer) service,
# ensures SMB2 is active, and opens the firewall rule.
# Must be run as Administrator.
# =============================================================================
#Requires -RunAsAdministrator

$ErrorActionPreference = "Stop"

function Log($msg) { Write-Host "[MediaStream] $msg" }
function Err($msg) { Write-Error "[MediaStream] ERROR: $msg"; exit 1 }

# ── Check elevation ───────────────────────────────────────────────────────────
$identity  = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]$identity
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Err "This script must be run as Administrator. Right-click PowerShell → Run as Administrator."
}

Log "Running as Administrator — OK"

# ── Enable SMB2/3 (disable SMB1 for security) ─────────────────────────────────
Log "Ensuring SMB2 protocol is enabled..."
try {
    Set-SmbServerConfiguration -EnableSMB2Protocol $true -Force
    Log "SMB2 protocol: ENABLED"
} catch {
    Log "WARNING: Could not configure SMB2 (may already be enabled): $_"
}

Log "Disabling legacy SMB1 for security..."
try {
    Set-SmbServerConfiguration -EnableSMB1Protocol $false -Force
    Log "SMB1 protocol: DISABLED"
} catch {
    Log "WARNING: Could not disable SMB1 (may already be disabled): $_"
}

# ── Enable and start LanmanServer service ─────────────────────────────────────
Log "Configuring LanmanServer (File and Printer Sharing) service..."
$svc = Get-Service -Name LanmanServer -ErrorAction SilentlyContinue
if ($null -eq $svc) {
    Err "LanmanServer service not found — this is unexpected on a standard Windows installation."
}

Set-Service -Name LanmanServer -StartupType Automatic
Log "LanmanServer startup type set to Automatic"

if ($svc.Status -ne "Running") {
    Log "Starting LanmanServer..."
    Start-Service -Name LanmanServer
    Start-Sleep -Seconds 2
}

$svc.Refresh()
if ($svc.Status -eq "Running") {
    Log "LanmanServer: RUNNING"
} else {
    Err "LanmanServer failed to start. Status: $($svc.Status)"
}

# ── Open firewall rule ────────────────────────────────────────────────────────
Log "Enabling 'File and Printer Sharing' firewall rules..."
try {
    Get-NetFirewallRule -DisplayGroup "File and Printer Sharing" |
        Set-NetFirewallRule -Enabled True
    Log "Firewall rules: ENABLED"
} catch {
    Log "WARNING: Could not enable firewall rules via NetFirewallRule — trying netsh..."
    netsh advfirewall firewall set rule group="File and Printer Sharing" new enable=Yes
}

# ── Summary ───────────────────────────────────────────────────────────────────
Log ""
Log "======================================================"
Log "  Windows SMB2/3 setup complete."
Log "  MediaStream will use 'net share' to create shares."
Log "  Clients connect via: \\<server-ip>\MEDIAFILES"
Log "  (Port 445 — standard Windows SMB, no special port)"
Log "======================================================"
Log "Restart MediaStream and click 'Start SMB' in the web UI."
