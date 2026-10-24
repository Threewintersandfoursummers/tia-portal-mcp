# tia-portal-mcp

MCP（Model Context Protocol）服务器：让 AI 助手通过西门子官方 Openness API 驱动 **TIA Portal V19**，在对话里完成打开工程、枚举 PLC 设备/程序块、导出块、编译、查询状态等操作。

## 架构

```
AI 客户端 ──stdio(MCP JSON-RPC)──▶ tia_mcp_server.py
                                       │  subprocess
                                       ▼
                            tia_host.ps1（常驻 PowerShell 桥）
                                       │  TIA Portal Openness API (C#)
                                       ▼
                              TIA Portal V19 实例
```

- `tia_mcp_server.py`：MCP stdio 服务器，**纯 Python 标准库，无 pip 依赖**
- `tia_host.ps1`：常驻 Openness 桥，整个会话只启动一次 TIA，按命令分发
- `tia_boot.ps1`：TIA 启动辅助脚本
- `tiaclient.py` / `simaticml.py`：SimaticML / 客户端辅助库

## 需求

- Windows + TIA Portal V19（含 Openness API：`Portal V19\PublicAPI\V19`）
- Windows PowerShell 5.1 x64
- 启动账号必须属于本地 Windows 组 **"Siemens TIA Openness"**（加入后需注销重登一次才生效）：
  ```
  net localgroup "Siemens TIA Openness" /add
  net localgroup "Siemens TIA Openness" "%COMPUTERNAME%\%USERNAME%" /add
  ```
- 注意：通过源码生成块（下载块到 PLC）需要 Openness 许可证；读取 / 导出 / 编译是基础 Openness，通常无需额外许可。

## 暴露的 MCP 工具（9 个）

| 工具 | 说明 |
|---|---|
| `tia_init` | 无界面模式启动 TIA Portal |
| `tia_open_project` | 打开 / 创建工程（path, create_if_missing） |
| `tia_list_devices` | 列出工程内设备/PLC |
| `tia_list_blocks` | 列出设备的 OB/FB/FC/DB |
| `tia_export_block` | 导出块（.xml/.scl/.awl） |
| `tia_compile` | 编译设备或整个工程 |
| `tia_project_info` | 工程概要 |
| `tia_close` | 关闭工程（save: bool） |
| `tia_status` | 宿主 / TIA / 工程状态 |

## 接入 MCP 客户端

在 `mcp.json`（Claude / WorkBuddy / 其他支持 stdio MCP 的客户端）里加：

```json
{
  "mcpServers": {
    "tia-portal": {
      "command": "python",
      "args": ["C:/path/to/tia-portal-mcp/tia_mcp_server.py"]
    }
  }
}
```

## 故障排查

- **Host timeout**：TIA 启动超过 300s（机器慢 / 许可弹窗），检查 Automation License Manager。
- **No project open**：先调 `tia_open_project`。
- **`Owner ... is not member of the windows group 'Siemens TIA Openness'`**：按上面命令加组，并**注销重登**（组成员身份在登录时写入进程令牌）。验证：`whoami /groups | findstr "Siemens TIA Openness"`。
- **Block not found**：块名区分大小写和空格，用 `tia_list_blocks` 返回的精确名称。
- Openness 注册表项需存在：`HKLM\SOFTWARE\Siemens\Automation\Openness\19.0`。
