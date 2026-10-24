# tia_host.ps1 - TIA Portal V19 Openness bridge (JSON-over-stdio, newline framed)
# One JSON request per line on stdin -> one JSON response per line on stdout.
#   Request : {"id":<n>,"method":"<name>","params":{...}}
#   Response: {"id":<n>,"ok":true,"result":{...}} | {"id":<n>,"ok":false,"error":"..."}
# Only JSON goes to stdout; diagnostics go to stderr.
# NOTE: ASCII only in this file (PowerShell 5.1 reads BOM-less files as ANSI).

$ErrorActionPreference = 'Stop'

# Raw UTF-8 streams: [Console]::In/Out go through the console code page, which
# corrupts Chinese text inside the JSON payloads.
$script:utf8 = New-Object System.Text.UTF8Encoding $false
$script:reader = New-Object System.IO.StreamReader([Console]::OpenStandardInput(), $script:utf8)
$script:writer = New-Object System.IO.StreamWriter([Console]::OpenStandardOutput(), $script:utf8)
$script:writer.AutoFlush = $true

. "$PSScriptRoot\tia_boot.ps1"

$script:tia = $null
$script:project = $null

function Ensure-Tia {
    param([switch]$Ui)
    if ($script:tia -eq $null) {
        $script:tia = Connect-Tia -NoUi:(-not $Ui)
    }
}

function Find-Device($name) {
    if ($script:project -eq $null) { throw "No project open" }
    foreach ($d in $script:project.Devices) {
        if ($d.Name -eq $name) { return $d }
    }
    throw "Device '$name' not found"
}

function Find-Block($sw, $name) {
    foreach ($b in $sw.BlockGroup.Blocks) {
        if ($b.Name -eq $name) { return $b }
    }
    return $null
}

function New-ProjectAt($path) {
    $dir = Split-Path -Parent $path
    $name = Split-Path -Leaf $path
    if ([string]::IsNullOrEmpty($dir)) { $dir = (Get-Location).Path }
    # If the folder already holds <name>.ap19 we open it instead of failing.
    $candidate = Join-Path (Join-Path $dir $name) ($name + ".ap19")
    if (Test-Path $candidate) {
        return $script:tia.Projects.Open((New-Object System.IO.FileInfo($candidate)))
    }
    return $script:tia.Projects.Create((New-Object System.IO.DirectoryInfo($dir)), $name)
}

function Get-CompilerResult($sw) {
    try { return $sw.Compile() } catch { }
    $svc = $null
    try { $svc = Invoke-GetService $sw ([Siemens.Engineering.Compiler.ICompilable]) } catch { }
    if ($null -eq $svc) { throw "No compile service available on this PlcSoftware" }
    return $svc.Compile()
}

function Collect-CompilerMessages($node, $depth, $acc) {
    # CompilerResult messages form a tree: Description / Path / State are the
    # useful members, and children hang off .Messages
    $desc = ""; try { $desc = "$($node.Description)" } catch { }
    $path = ""; try { $path = "$($node.Path)" } catch { }
    $state = ""; try { $state = "$($node.State)" } catch { }
    if ($desc -ne "" -or $state -eq 'Error') {
        [void]$acc.Add(("    " * $depth) + "[" + $state + "] " + $path + " : " + $desc)
    }
    $kids = $null
    try { $kids = $node.Messages } catch { }
    if ($null -ne $kids) {
        foreach ($k in $kids) { Collect-CompilerMessages $k ($depth + 1) $acc }
    }
}

function Walk-DeviceItem($item, $depth, $acc) {
    $pos = ""; try { $pos = "$($item.PositionNumber)" } catch { }
    $ti = ""; try { $ti = "$($item.TypeIdentifier)" } catch { }
    [void]$acc.Add((("  " * $depth) + $item.Name + " | pos=" + $pos + " | " + $ti))
    try {
        foreach ($c in $item.DeviceItems) { Walk-DeviceItem $c ($depth + 1) $acc }
    } catch { }
}

function Find-ItemByName($device, $wanted) {
    # breadth-first search over the device-item tree, match on Name
    $queue = New-Object System.Collections.Queue
    foreach ($di in $device.DeviceItems) { $queue.Enqueue($di) }
    while ($queue.Count -gt 0) {
        $cur = $queue.Dequeue()
        if ($cur.Name -eq $wanted) { return $cur }
        try { foreach ($c in $cur.DeviceItems) { $queue.Enqueue($c) } } catch { }
    }
    return $null
}

function Handle($msg) {
    $id = $msg.id
    $method = $msg.method
    $p = $msg.params
    if ($null -eq $p) { $p = @{} }
    try {
        switch ($method) {

            'init' {
                Ensure-Tia -Ui:([bool]$p.ui)
                $mode = 'WithoutUserInterface'
                if ($p.ui) { $mode = 'WithUserInterface' }
                return @{id=$id; ok=$true; result=@{started=$true; mode=$mode}}
            }

            'create_project' {
                Ensure-Tia -Ui:([bool]$p.ui)
                if ([string]::IsNullOrEmpty($p.path)) { throw "path is required" }
                $script:project = New-ProjectAt $p.path
                $pp = ""; try { $pp = "$($script:project.Path)" } catch { }
                return @{id=$id; ok=$true; result=@{name=$script:project.Name; path=$pp}}
            }

            'open_project' {
                Ensure-Tia -Ui:([bool]$p.ui)
                if ([string]::IsNullOrEmpty($p.path)) { throw "path is required" }
                $projFile = $p.path
                if (Test-Path $p.path -PathType Container) {
                    $ap = Get-ChildItem $p.path -Filter *.ap19 | Select-Object -First 1
                    if ($null -eq $ap) { throw "No .ap19 found in '$($p.path)'" }
                    $projFile = $ap.FullName
                }
                $script:project = $script:tia.Projects.Open((New-Object System.IO.FileInfo($projFile)))
                $pp = ""; try { $pp = "$($script:project.Path)" } catch { }
                return @{id=$id; ok=$true; result=@{name=$script:project.Name; path=$pp}}
            }

            'save' {
                if ($script:project -eq $null) { throw "No project open" }
                $script:project.Save()
                $pp = ""; try { $pp = "$($script:project.Path)" } catch { }
                return @{id=$id; ok=$true; result=@{saved=$true; path=$pp}}
            }

            'add_device' {
                if ($script:project -eq $null) { throw "No project open" }
                $orders = @()
                if ($p.order_numbers) { $orders = @($p.order_numbers) }
                if ([string]::IsNullOrEmpty($p.name)) { throw "name is required" }
                $errors = @()
                foreach ($o in $orders) {
                    try {
                        $dev = $script:project.Devices.CreateWithItem($o, $p.name, $p.name)
                        $sw = Get-PlcSoftware $dev
                        return @{id=$id; ok=$true; result=@{name=$dev.Name; orderNumber=$o; hasPlcSoftware=($sw -ne $null)}}
                    } catch {
                        $errors += ("[" + $o + "] " + $_.Exception.Message)
                    }
                }
                throw ("add_device failed for all order numbers: " + ($errors -join " ;; "))
            }

            'list_devices' {
                if ($script:project -eq $null) { throw "No project open" }
                $list = @()
                foreach ($d in $script:project.Devices) {
                    $items = @()
                    foreach ($di in $d.DeviceItems) {
                        $items += @{name=$di.Name; type=$di.TypeIdentifier}
                    }
                    $list += @{name=$d.Name; type=$d.TypeIdentifier; items=$items}
                }
                return @{id=$id; ok=$true; result=@{devices=$list}}
            }

            'list_blocks' {
                if ($script:project -eq $null) { throw "No project open" }
                $sw = Get-PlcSoftware (Find-Device $p.device)
                if ($null -eq $sw) { throw "No PLC software on '$($p.device)'" }
                $blocks = @()
                foreach ($b in $sw.BlockGroup.Blocks) {
                    $lang = ""; try { $lang = $b.ProgrammingLanguage.ToString() } catch { }
                    $num = ""; try { $num = $b.Number } catch { }
                    $blocks += @{name=$b.Name; number=$num; language=$lang; typeName=$b.GetType().Name}
                }
                return @{id=$id; ok=$true; result=@{device=$p.device; blocks=$blocks}}
            }

            'list_tables' {
                if ($script:project -eq $null) { throw "No project open" }
                $sw = Get-PlcSoftware (Find-Device $p.device)
                $tables = @()
                foreach ($tt in $sw.TagTableGroup.TagTables) {
                    $tags = @()
                    foreach ($t in $tt.Tags) {
                        $adr = ""; try { $adr = $t.LogicalAddress } catch { }
                        $tags += @{name=$t.Name; dataType=$t.DataTypeName; address=$adr}
                    }
                    $tables += @{name=$tt.Name; tags=$tags}
                }
                return @{id=$id; ok=$true; result=@{tables=$tables}}
            }

            'add_tags' {
                if ($script:project -eq $null) { throw "No project open" }
                $sw = Get-PlcSoftware (Find-Device $p.device)
                if ($null -eq $sw) { throw "No PLC software on '$($p.device)'" }
                $table = $null
                if (-not [string]::IsNullOrEmpty($p.table)) {
                    foreach ($tt in $sw.TagTableGroup.TagTables) {
                        if ($tt.Name -eq $p.table) { $table = $tt; break }
                    }
                }
                if ($null -eq $table -and $sw.TagTableGroup.TagTables.Count -gt 0) {
                    $table = $sw.TagTableGroup.TagTables[0]
                }
                if ($null -eq $table) {
                    $tn = "Default tag table"
                    if (-not [string]::IsNullOrEmpty($p.table)) { $tn = $p.table }
                    $table = $sw.TagTableGroup.TagTables.Create($tn)
                }
                $added = 0; $skipped = 0; $updated = 0; $errs = @()
                foreach ($spec in $p.tags) {
                    $existing = $null
                    foreach ($t0 in $table.Tags) { if ($t0.Name -eq $spec.name) { $existing = $t0; break } }
                    if ($null -ne $existing) {
                        $skipped++
                        # keep addresses/comments in sync on rebuild
                        try {
                            if ("$($existing.LogicalAddress)" -ne $spec.address) {
                                $existing.LogicalAddress = $spec.address
                                $updated++
                            }
                            if ($spec.comment -and "$($existing.Comment)" -ne $spec.comment) {
                                $existing.Comment = $spec.comment
                            }
                        } catch { $errs += ("[" + $spec.name + "] update: " + $_.Exception.Message) }
                        continue
                    }
                    try {
                        $tag = $table.Tags.Create($spec.name, $spec.type, $spec.address)
                        if ($spec.comment) {
                            try { $tag.Comment = $spec.comment } catch { }
                        }
                        $added++
                    } catch {
                        $errs += ("[" + $spec.name + "] " + $_.Exception.Message)
                    }
                }
                return @{id=$id; ok=$true; result=@{table=$table.Name; added=$added; skipped=$skipped; updated=$updated; errors=$errs}}
            }

            'set_tag_comments' {
                if ($script:project -eq $null) { throw "No project open" }
                $sw = Get-PlcSoftware (Find-Device $p.device)
                if ($null -eq $sw) { throw "No PLC software on '$($p.device)'" }
                $n = 0; $errs = @()
                foreach ($tt in $sw.TagTableGroup.TagTables) {
                    foreach ($t0 in $tt.Tags) {
                        $spec = $null
                        foreach ($s0 in $p.tags) { if ($s0.name -eq $t0.Name) { $spec = $s0; break } }
                        if ($null -eq $spec -or [string]::IsNullOrEmpty($spec.comment)) { continue }
                        try {
                            $cm = $t0.Comment
                            if ($null -ne $cm -and $cm.Items.Count -gt 0) {
                                $cm.Items[0].Text = $spec.comment
                                $n++
                            }
                        } catch { $errs += ("[" + $t0.Name + "] " + $_.Exception.Message) }
                    }
                }
                return @{id=$id; ok=$true; result=@{updated=$n; errors=$errs}}
            }

            'delete_tags' {
                if ($script:project -eq $null) { throw "No project open" }
                $sw = Get-PlcSoftware (Find-Device $p.device)
                if ($null -eq $sw) { throw "No PLC software on '$($p.device)'" }
                $n = 0; $errs = @()
                foreach ($tt in $sw.TagTableGroup.TagTables) {
                    $kill = @()
                    foreach ($t0 in $tt.Tags) {
                        foreach ($nm in @($p.names)) {
                            if ($t0.Name -eq $nm) { $kill += $t0; break }
                        }
                    }
                    foreach ($t0 in $kill) {
                        try { $t0.Delete(); $n++ }
                        catch { $errs += ("[" + $t0.Name + "] " + $_.Exception.Message) }
                    }
                }
                return @{id=$id; ok=$true; result=@{deleted=$n; errors=$errs}}
            }

            'import_blocks' {
                if ($script:project -eq $null) { throw "No project open" }
                $sw = Get-PlcSoftware (Find-Device $p.device)
                if ($null -eq $sw) { throw "No PLC software on '$($p.device)'" }
                $done = @(); $errs = @()
                foreach ($f in @($p.files)) {
                    try {
                        $fi = New-Object System.IO.FileInfo($f)
                        $blk = $sw.BlockGroup.Blocks.Import($fi, [Siemens.Engineering.ImportOptions]::Override)
                        $done += @{file=(Split-Path -Leaf $f); block=$blk.Name}
                    } catch {
                        $m = $_.Exception.Message
                        if ($_.Exception.InnerException) { $m = $m + " | " + $_.Exception.InnerException.Message }
                        $errs += ("[" + (Split-Path -Leaf $f) + "] " + $m)
                    }
                }
                return @{id=$id; ok=$true; result=@{imported=$done; errors=$errs}}
            }

            'export_block' {
                if ($script:project -eq $null) { throw "No project open" }
                $sw = Get-PlcSoftware (Find-Device $p.device)
                $blk = Find-Block $sw $p.block
                if ($null -eq $blk) { throw "Block '$($p.block)' not found" }
                # V19: Export(FileInfo, ExportOptions) - ExportOptions has no "Overwrite"
                $fi = New-Object System.IO.FileInfo($p.out_path)
                if ($fi.Exists) { Remove-Item -LiteralPath $fi.FullName -Force }
                $blk.Export($fi, [Siemens.Engineering.ExportOptions]::WithDefaults)
                return @{id=$id; ok=$true; result=@{exported=$p.out_path}}
            }

            'delete_block' {
                if ($script:project -eq $null) { throw "No project open" }
                $sw = Get-PlcSoftware (Find-Device $p.device)
                $blk = Find-Block $sw $p.block
                if ($null -ne $blk) { $blk.Delete() }
                return @{id=$id; ok=$true; result=@{deleted=($null -ne $blk)}}
            }

            'create_instance_db' {
                if ($script:project -eq $null) { throw "No project open" }
                $sw = Get-PlcSoftware (Find-Device $p.device)
                if ($null -eq $sw) { throw "No PLC software on '$($p.device)'" }
                $existing = $null
                foreach ($b in $sw.BlockGroup.Blocks) {
                    if ($b.Name -eq $p.name) { $existing = $b; break }
                }
                if ($null -ne $existing) {
                    return @{id=$id; ok=$true; result=@{name=$existing.Name; created=$false}}
                }
                $num = 1
                if ($p.number) { $num = [int]$p.number }
                $db = $sw.BlockGroup.Blocks.CreateInstanceDB($p.name, $true, $num, $p.instance_of)
                return @{id=$id; ok=$true; result=@{name=$db.Name; created=$true}}
            }

            'import_watchtable' {
                if ($script:project -eq $null) { throw "No project open" }
                $sw = Get-PlcSoftware (Find-Device $p.device)
                if ($null -eq $sw) { throw "No PLC software on '$($p.device)'" }
                $fi = New-Object System.IO.FileInfo($p.file)
                $wt = $sw.WatchAndForceTableGroup.WatchTables.Import($fi, [Siemens.Engineering.ImportOptions]::Override)
                $n = 0
                try { $n = $wt.Entries.Count } catch { }
                return @{id=$id; ok=$true; result=@{name=$wt.Name; consistent=("$($wt.IsConsistent)"); entries=$n}}
            }

            'list_watchtables' {
                if ($script:project -eq $null) { throw "No project open" }
                $sw = Get-PlcSoftware (Find-Device $p.device)
                $names = @()
                foreach ($w in $sw.WatchAndForceTableGroup.WatchTables) { $names += $w.Name }
                return @{id=$id; ok=$true; result=@{tables=$names}}
            }

            'device_tree' {
                if ($script:project -eq $null) { throw "No project open" }
                $dev = Find-Device $p.device
                $acc = New-Object System.Collections.ArrayList
                Walk-DeviceItem $dev 0 $acc
                return @{id=$id; ok=$true; result=@{device=$dev.Name; tree=@($acc)}}
            }

            'add_module' {
                if ($script:project -eq $null) { throw "No project open" }
                $dev = Find-Device $p.device
                $parent = $null
                if (-not [string]::IsNullOrEmpty($p.parent)) {
                    $parent = Find-ItemByName $dev $p.parent
                    if ($null -eq $parent) { throw "Parent item '$($p.parent)' not found" }
                } else {
                    $parent = $dev.DeviceItems[0]
                }
                $pos = 1
                if ($p.position) { $pos = [int]$p.position }
                $errors = @()
                foreach ($o in @($p.order_numbers)) {
                    try {
                        $mi = $parent.PlugNew($o, $p.name, $pos)
                        return @{id=$id; ok=$true; result=@{name=$mi.Name; orderNumber=$o; position=$mi.PositionNumber; parent=$parent.Name}}
                    } catch {
                        $m = $_.Exception.Message
                        if ($_.Exception.InnerException) { $m = $m + " | " + $_.Exception.InnerException.Message }
                        $errors += ("[" + $o + "] " + $m)
                    }
                }
                throw ("add_module failed: " + ($errors -join " ;; "))
            }

            'compile_hw' {
                if ($script:project -eq $null) { throw "No project open" }
                $dev = Find-Device $p.device
                $acc = New-Object System.Collections.ArrayList
                $states = @()
                foreach ($di in $dev.DeviceItems) {
                    $svc = $null
                    try { $svc = Invoke-GetService $di ([Siemens.Engineering.Compiler.ICompilable]) } catch { }
                    if ($null -eq $svc) { continue }
                    try {
                        $r = $svc.Compile()
                        $st = ""; try { $st = "$($r.State)" } catch { }
                        $states += @{item=$di.Name; state=$st}
                        Collect-CompilerMessages $r 0 $acc
                    } catch {
                        [void]$acc.Add("[THREW] " + $_.Exception.Message)
                    }
                }
                return @{id=$id; ok=$true; result=@{states=$states; messages=@($acc)}}
            }

            'compile' {
                if ($script:project -eq $null) { throw "No project open" }
                if ($p.target -and $p.target -ne 'project') {
                    $sw = Get-PlcSoftware (Find-Device $p.target)
                    if ($null -eq $sw) { throw "No PLC software on '$($p.target)'" }
                    $res = Get-CompilerResult $sw
                } else {
                    $res = $script:project.Compile()
                }
                $state = ""; try { $state = $res.State.ToString() } catch { $state = "$($res.State)" }
                $acc = New-Object System.Collections.ArrayList
                Collect-CompilerMessages $res 0 $acc
                $ec = 0; try { $ec = $res.ErrorCount } catch { }
                $wc = 0; try { $wc = $res.WarningCount } catch { }
                return @{id=$id; ok=$true; result=@{state=$state; errorCount=$ec; warningCount=$wc; messages=@($acc)}}
            }

            'project_info' {
                if ($script:project -eq $null) { throw "No project open" }
                $pp = ""; try { $pp = "$($script:project.Path)" } catch { }
                return @{id=$id; ok=$true; result=@{name=$script:project.Name; path=$pp; deviceCount=$script:project.Devices.Count}}
            }

            'close' {
                if ($script:project -ne $null) {
                    if ($p.save) { try { $script:project.Save() } catch { } }
                    try { $script:project.Close() } catch { }
                    $script:project = $null
                }
                return @{id=$id; ok=$true; result=@{closed=$true}}
            }

            'attach_project' {
                # The TIA instance may already hold a project opened by an earlier
                # client process; adopt it instead of trying to Open it again.
                Ensure-Tia
                if ($script:tia.Projects.Count -eq 0) { throw "No project open in this TIA instance" }
                $script:project = $script:tia.Projects[0]
                $pp = ""; try { $pp = "$($script:project.Path)" } catch { }
                return @{id=$id; ok=$true; result=@{name=$script:project.Name; path=$pp}}
            }

            'status' {
                $pname = $null
                if ($script:project -ne $null) { try { $pname = $script:project.Name } catch { } }
                return @{id=$id; ok=$true; result=@{tia_started=($script:tia -ne $null); project_open=($script:project -ne $null); project_name=$pname}}
            }

            default { return @{id=$id; ok=$false; error="Unknown method: $method"} }
        }
    } catch {
        $err = $_.Exception.Message
        if ($_.Exception.InnerException) { $err = $err + " | " + $_.Exception.InnerException.Message }
        return @{id=$id; ok=$false; error=$err}
    }
}

$script:writer.WriteLine((ConvertTo-Json @{ready=$true} -Compress))
$script:writer.Flush()

while ($true) {
    $line = $script:reader.ReadLine()
    if ($null -eq $line) { break }
    $line = $line.Trim()
    if ($line -eq '') { continue }
    try { $msg = $line | ConvertFrom-Json }
    catch {
        $script:writer.WriteLine((ConvertTo-Json @{id=$null; ok=$false; error="bad json"} -Compress))
        $script:writer.Flush(); continue
    }
    if ($msg.method -eq 'shutdown') {
        try { if ($script:project -ne $null) { $script:project.Save() } } catch { }
        try { if ($script:project -ne $null) { $script:project.Close() } } catch { }
        try { if ($script:tia -ne $null) { $script:tia.Dispose() } } catch { }
        $script:writer.WriteLine((ConvertTo-Json @{id=$msg.id; ok=$true; result=@{shutdown=$true}} -Compress))
        $script:writer.Flush(); break
    }
    $resp = Handle $msg
    $script:writer.WriteLine((ConvertTo-Json $resp -Compress -Depth 10))
    $script:writer.Flush()
}
