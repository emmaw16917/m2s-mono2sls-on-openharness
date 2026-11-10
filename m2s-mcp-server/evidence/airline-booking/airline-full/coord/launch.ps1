# Start launch.sh outside the caller's process tree (WMI creates it), so the run survives
# the Claude Code session ending; the console window is hidden.
param([string]$StopAfter = "")
$bash = "C:\Program Files\Git\bin\bash.exe"
$script = "D:/Emma/MyLab/Mono2SLs/Mono2Sls-artifacts/M2S/runs/airline-full/coord/launch.sh"
$cmd = "`"$bash`" `"$script`""
if ($StopAfter) { $cmd += " --stop-after $StopAfter" }
$startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ ShowWindow = [uint16]0 }
$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine = $cmd; CurrentDirectory = "D:\Emma\MyLab\Mono2SLs-v2\m2s-mcp-server";
    ProcessStartupInformation = $startup }
"ReturnValue=$($r.ReturnValue) ProcessId=$($r.ProcessId)"
