# 采集命令配置与输出校验设计

## 背景

真实采集 HQ-R1（IOSv 路由器）时执行了 `show vlan brief`，设备返回：

```text
% Invalid input detected at '^' marker.
```

但采集结果仍记为 `success`。原因：

1. `READONLY_COMMANDS["cisco_ios"]` 同时服务路由器和交换机；
2. `collect_device()` 只判断 `send_command` 是否抛异常，不检查 Cisco 错误提示。

这会让“命令不支持”变成成功证据，污染设备状态和诊断。

## 目标

1. 按设备角色/型号选择命令集：路由器不执行 `show vlan brief`；
2. 交换机保留 `show vlan brief`；
3. 识别 `% Invalid input`、`% Incomplete command`、`% Ambiguous command`、
   `% Unrecognized command`，记为失败并保留原始输出；
4. 不改变只读白名单的安全边界；
5. 现有采集/诊断/DTO 兼容。

## 非目标

- 不改 SNMP 采集；
- 不改设备台账结构；
- 不实现自动故障注入；
- 不重构整个 collector。
