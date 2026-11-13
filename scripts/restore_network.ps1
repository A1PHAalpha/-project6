Enable-NetAdapter -Name 'VMware Network Adapter VMnet1' -Confirm:$false
route delete 192.168.249.0 > $null 2>&1
Write-Output 'VMnet1 re-enabled, temp route removed'
