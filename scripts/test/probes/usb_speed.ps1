Get-PnpDevice -Class USB |
    Where-Object { $_.InstanceId -match 'VID_3707|VID_1BCF' -or $_.Name -match 'Hub|Controller|Host' } |
    Select-Object Name, Status, InstanceId |
    Format-Table -AutoSize
