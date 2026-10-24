================================================================
 TIA Portal V19 <-> WorkBuddy AI  MCP Connector
================================================================

WHAT THIS IS
  A local MCP (Model Context Protocol) server that lets WorkBuddy
  drive Siemens TIA Portal V19 through its official Openness API.
  WorkBuddy can open projects, list PLC devices/blocks, export
  blocks, compile, and report results - all from chat.

FILES (under @"C:\Users\Administrator\.workbuddy\mcp-servers\tia-portal")
  tia_mcp_server.py  = MCP stdio server (pure Python stdlib, no pip)
  tia_host.ps1       = Openness bridge (keeps a live TIA session)
  README.txt         = this file

REQUIREMENTS
  - TIA Portal V19 installed (confirmed at:
    @"C:\Program Files\Siemens\Automation\Portal V19")
  - Openness API present (confirmed at:
    @"C:\Program Files\Siemens\Automation\Portal V19\PublicAPI\V19")
  - Windows PowerShell 5.1 x64 (system powershell.exe)
  - TIA Portal must NOT be running a conflicting instance.
  - NOTE: code-generation (creating blocks from source) needs an
    Openness license. Read / export / compile are base Openness and
    normally work without extra licensing.

HOW IT IS WIRED
  Registered in @"C:\Users\Administrator\.workbuddy\mcp.json" as
  server "tia-portal". WorkBuddy launches it automatically; you do
  not run it by hand.

TOOLS EXPOSED TO WORKBUDDY
  tia_init                  start TIA headless
  tia_open_project          open/create a project (path, create_if_missing)
  tia_list_devices          list devices in open project
  tia_list_blocks           list OB/FB/FC/DB of a device
  tia_export_block          export a block (.xml/.scl/.awl)
  tia_compile               compile device or whole project
  tia_project_info          open project summary
  tia_close                 close project (save: bool)
  tia_status                host / TIA / project status

USAGE IN CHAT
  Just tell WorkBuddy what you want, e.g.:
   "Open the TIA project at D:\Projects\Line1 and list its PLC blocks"
   "Export FB1 from PLC_1 to C:\temp\fb1.scl then compile the project"

LIFECYCLE
  The PowerShell bridge stays alive for the whole WorkBuddy session.
  TIA is launched lazily on the first open/create. To fully clean up,
  call tia_close then tia_status, or simply close WorkBuddy (the
  bridge disposes TIA on shutdown).

TROUBLESHOOTING
  - "Host timeout"  -> TIA took >300s to launch (slow PC / license
                       prompt). Check Automation License Manager.
  - "No project open" -> call tia_open_project first.
  - SECURITY GROUP (IMPORTANT): TIA Portal Openness requires the
    launching account to be a member of the local Windows group
    "Siemens TIA Openness". If you see
    "Owner '...' is not member of the windows group 'Siemens TIA
    Openness'", run (as admin):
        net localgroup "Siemens TIA Openness" /add
        net localgroup "Siemens TIA Openness" "%COMPUTERNAME%\%USERNAME%" /add
    GROUP MEMBERSHIP IS EVALUATED AT LOGON. After adding the user you
    MUST log off and back on (or reboot) once so the new process token
    contains the group - otherwise Openness still refuses to start TIA,
    even though the user is already a member. Verify with:
        whoami /groups | findstr "Siemens TIA Openness"
    The group must appear before the connector can launch TIA.
  - "Block not found" -> block name is case/space sensitive; use the
                       exact name from tia_list_blocks.
  - Openness must be enabled (registry
    HKLM\SOFTWARE\Siemens\Automation\Openness\19.0 is present).
================================================================
