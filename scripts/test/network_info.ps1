Get-NetAdapter | Where-Object { $_.Status -eq 'Up' } | Select-Object Name, InterfaceDescription, LinkSpeed, MediaConnectionState | Format-Table -AutoSize
Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -like '192.168.22.*' } | Select-Object InterfaceAlias, IPAddress, PrefixLength | Format-Table -AutoSize
