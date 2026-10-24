# tia_boot.ps1 - shared bootstrap for TIA Portal V19 Openness scripts.
# Dot-source this file:  . "$PSScriptRoot\tia_boot.ps1"
# Then call:  $tia = Connect-Tia            (attach to running UI instance)
#             $tia = Connect-Tia -NoUi      (headless instance)

$ErrorActionPreference = 'Stop'

$script:TiaApiRoot = "C:\Program Files\Siemens\Automation\Portal V19\PublicAPI\V19"
$script:TiaEngDll  = Join-Path $script:TiaApiRoot "Siemens.Engineering.dll"
$script:TiaHmiDll  = Join-Path $script:TiaApiRoot "Siemens.Engineering.Hmi.dll"

# The Siemens.* resolver must live ONLY during Add-Type of the facade.
# If left attached, TIA's internal assembly loading re-enters it unboundedly
# and the process dies with StackOverflowException ("无法创建新的堆栈防护页面").
$global:tiaResolving = @{}
$global:tiaResolveHandler = {
    param($sender, $e)
    $name = $e.Name.Split(',')[0]
    if (-not $name.StartsWith('Siemens.')) { return $null }
    if ($global:tiaResolving.ContainsKey($name)) { return $null }
    try {
        $global:tiaResolving[$name] = $true
        foreach ($dir in @(
            "C:\Program Files\Siemens\Automation\Portal V19\PublicAPI\V19",
            "C:\Program Files\Siemens\Automation\Portal V19\Bin\PublicAPI",
            "C:\Program Files\Siemens\Automation\Portal V19\Bin"
        )) {
            $cand = Join-Path $dir ($name + ".dll")
            if (Test-Path $cand) { return [System.Reflection.Assembly]::LoadFrom($cand) }
        }
        return $null
    } finally {
        $global:tiaResolving.Remove($name) | Out-Null
    }
} -as [System.ResolveEventHandler]

[System.AppDomain]::CurrentDomain.add_AssemblyResolve($global:tiaResolveHandler)
Add-Type -Path $script:TiaEngDll
if (Test-Path $script:TiaHmiDll) { Add-Type -Path $script:TiaHmiDll }
[System.AppDomain]::CurrentDomain.remove_AssemblyResolve($global:tiaResolveHandler)

function Connect-Tia {
    param([switch]$NoUi, [switch]$ForceNew)
    $mode = [Siemens.Engineering.TiaPortalMode]::WithUserInterface
    if ($NoUi) { $mode = [Siemens.Engineering.TiaPortalMode]::WithoutUserInterface }
    # Reuse an already running instance instead of spawning a second TIA
    # (each instance costs >1 GB and a separate Openness session).
    if (-not $ForceNew) {
        try {
            $running = [Siemens.Engineering.TiaPortal]::GetProcesses()
            if ($null -ne $running -and $running.Count -gt 0) {
                foreach ($rp in $running) {
                    try { return $rp.Attach() } catch { }
                }
            }
        } catch { }
    }
    return New-Object Siemens.Engineering.TiaPortal($mode)
}

# EngineeringObject.GetService<T>()  -- invoked via reflection, PS has no generic call syntax in 5.1
function Invoke-GetService {
    param($Target, $ServiceType)
    $m = $Target.GetType().GetMethods() |
        Where-Object { $_.Name -eq 'GetService' -and $_.IsGenericMethodDefinition -and $_.GetParameters().Count -eq 0 } |
        Select-Object -First 1
    if ($null -eq $m) { return $null }
    return $m.MakeGenericMethod($ServiceType).Invoke($Target, @())
}

function Get-PlcSoftware {
    param($Device)
    # GetService<T>() carries the constraint "where T : IEngineeringService", and
    # PlcSoftware is NOT an IEngineeringService. The supported route is
    # SoftwareContainer (which IS a service) and then its .Software property.
    $targets = @()
    foreach ($di in $Device.DeviceItems) { $targets += $di }
    $targets += $Device
    foreach ($t in $targets) {
        try {
            $sc = Invoke-GetService $t ([Siemens.Engineering.HW.Features.SoftwareContainer])
            if ($null -ne $sc) {
                $soft = $sc.Software
                if ($null -ne $soft -and $soft.GetType().Name -eq 'PlcSoftware') { return $soft }
            }
        } catch { }
    }
    return $null
}

function Get-PlcSoftwareOfProject {
    param($Project)
    foreach ($dev in $Project.Devices) {
        $sw = Get-PlcSoftware $dev
        if ($null -ne $sw) { return $sw }
    }
    return $null
}
