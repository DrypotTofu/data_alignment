Get-PnpDevice -Class USB | Where-Object { $_.InstanceId -match 'VID_3707|VID_1BCF' } | Select-Object Name, Status, InstanceId | Format-Table -AutoSize
