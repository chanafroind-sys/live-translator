# Set the Windows default playback / recording device by (partial) name.
#   powershell -ExecutionPolicy Bypass -File tools\set_default_audio.ps1 -Playback "Speakers" -Recording "Microphone Array"
param([string]$Playback = "", [string]$Recording = "")

Add-Type @"
using System; using System.Runtime.InteropServices;
[Guid("f8679f50-850a-41cf-9c72-430f290290c8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IPolicyConfig {
  int a(); int b(); int c(); int d(); int e(); int f(); int g(); int h(); int i(); int j();
  [PreserveSig] int SetDefaultEndpoint([MarshalAs(UnmanagedType.LPWStr)] string id, int role);
}
[ComImport, Guid("870af99c-171d-4f9e-af0d-e63df40c2bc9")] class CPolicyConfigClient {}
public static class AudioDefault {
  public static void Set(string id) {
    var p = (IPolicyConfig)new CPolicyConfigClient();
    for (int role = 0; role < 3; role++) Marshal.ThrowExceptionForHR(p.SetDefaultEndpoint(id, role));
  }
}
"@

function Find-Device($flow, $name) {
    $root = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\$flow"
    $prefix = if ($flow -eq "Render") { "{0.0.0.00000000}." } else { "{0.0.1.00000000}." }
    foreach ($k in Get-ChildItem $root) {
        if ((Get-ItemProperty $k.PSPath).DeviceState -ne 1) { continue }  # active only
        $props = Get-ItemProperty "$($k.PSPath)\Properties"
        $friendly = $props.'{a45c254e-df1c-4efd-8020-67d146a850e0},2'   # device name
        $iface = $props.'{b3f8fa53-0004-438e-9003-51a46e139bfc},6'      # adapter name
        if ("$friendly $iface" -like "*$name*") { return @{ Id = $prefix + $k.PSChildName; Name = "$friendly ($iface)" } }
    }
    throw "No active $flow device matching '$name'"
}

if ($Playback)  { $d = Find-Device "Render"  $Playback;  [AudioDefault]::Set($d.Id); "Playback  -> $($d.Name)" }
if ($Recording) { $d = Find-Device "Capture" $Recording; [AudioDefault]::Set($d.Id); "Recording -> $($d.Name)" }
