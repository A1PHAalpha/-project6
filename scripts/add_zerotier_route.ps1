route delete 192.168.249.0 > $null 2>&1
route add 192.168.249.0 mask 255.255.255.0 10.10.146.218 metric 10
Write-Output 'route added'
route print 192.168.249
