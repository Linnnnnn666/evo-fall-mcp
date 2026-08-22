# 新板接入 SOP（陌生 ESP 板全流程）

> 目标：任何一块新的 ESP32 系列板，按本流程走一遍后即可**拔线离线、纯无线维护**（OTA 迭代）。
> 核心原则：**遥测通了不算接入完成，OTA 往返验证通过才算。**

## 准备（约 1 分钟）
1. USB 接线，设备管理器确认串口（如 COMxx）
2. 识别芯片：`esptool --chip auto -p COMxx flash_id`（拿 flash 容量）+ `read_mac`（拿 MAC）
3. 确认板子外设：板载 LED GPIO、有无传感器/继电器等

## 一、登记（语音即可）
对语音助手说：**"接入一块新板，叫 board-xxx，MAC 是 xx:xx，型号 xxx"**
→ DSH 调 `dev_register_board` 登记（自动落盘 devices.json，返回 4 步指引）

## 二、建板配置 boards/<device_id>.h（DSH 代劳）
在 `/opt/firmware/board-template/main/boards/` 复制 `board-s3-36ac.h` → `<device_id>.h`，改：
- `BOARD_DEVICE_ID` / `BOARD_MODEL`
- `BOARD_WIFI_SSID` / `BOARD_WIFI_PASSWORD`（板子所在 WiFi）
- `BOARD_LED_GPIO`（板载 LED，没有就留 48 或 -1）
- `BOARD_FW_VERSION` = "0.1.0"

> 只需建这个文件，`dev_ota_deploy` 的工程规格是**从 boards/ 目录动态发现**的，零代码改动。
> 告诉 DSH "按 board-s3-36ac.h 模板给 board-xxx 建配置，WiFi 是 xxx，密码 xxx" 即可代劳。

## 三、烧录引导固件（唯一必须连线的步骤）
```bash
# 服务器编译
bash /opt/firmware/board-template/build_board.sh <device_id>
# 产物 scp 回本地电脑，esptool 烧录（flash 参数已由 sdkconfig.defaults 固化为 DIO/40M/16MB 保守组合）
esptool --chip esp32s3 -p COMxx write_flash --flash_mode dio --flash_freq 40m --flash_size 16MB \
  0x0 bootloader.bin 0x8000 partition-table.bin 0xf000 ota_data_initial.bin 0x20000 board_template.bin
```

## 四、验收（缺一不可）
1. **串口日志**：启动 → `Wi-Fi connected` → `MQTT 已连接` → `订阅命令主题`（rc 正常）
2. **遥测**：`mosquitto_sub -t fall/telemetry/<device_id>` 能看到 10s 周期数据
3. **【必做】OTA 往返**：发布 v0.1.1 → 推 `ota_check` 命令 → 串口看到下载/校验/重启 → 新版本跑起来 → 再次检查打印"固件已是最新"
4. **标记完成**：DSH 更新 devices.json `ota_verified=true`（语音说"板子验证好了"）

## 五、之后全无线
- 改功能：`dev_ota_deploy(board_id=<device_id>, requirement="...")` → DSH 改码编译归档推送 → 板子自动升级
- 查状态：`dev_list_boards`（语音"板子们怎么样"）
- 自动检查：板子开机 30s + 每 6h 自动查版本；也可随时推 `ota_check`

## 已自动化（新板零配置项）
| 环节 | 说明 |
|---|---|
| MQTT ACL | `fall/commands/#` 通配，新板自动可收命令 |
| OTA 静态服务 | `/firmware/<id>/latest.json` 通配，建目录即可 |
| 固件工程规格 | boards/ 目录动态发现，`dev_ota_deploy` 自动认识 |
| flash 参数 | sdkconfig.defaults 固化 DIO/40M，规避劣质 flash 兼容坑 |
| 回滚保护 | 双分区 + 失败自动回滚，OTA 失败不变砖 |

## 需要人工
- 物理接线 / 串口确认 / 首次烧录
- 板子特有信息：WiFi 凭据、LED GPIO、外设接线
