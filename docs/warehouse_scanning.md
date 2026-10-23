# 仓库扫码置货与发货

## 设计结论

- 一张“销售出库作业单”同时打印两个 Code128 条码：置货码 `YCP01...`、发货码 `YCS01...`。
- 条码只保存随机任务令牌，不包含设备认证码、客户信息或可直接访问的 URL。
- 新大陆 OY10 Plus 使用 USB-HID 键盘模式，并配置扫描后自动发送 Enter；Windows 不需要安装扫码 SDK。
- 每次扫码都代表该订单全部未完成数量。库存、包装、财务、质检、照片或仓库权限任一条件不满足时，整单失败，不处理部分数量。
- 发货必须先拍照。客户端从配置目录中选择最新、未使用且仍在有效时间内的 JPG/PNG，先上传凭证，再调用整单发货接口。
- 作业单绑定一个仓库。扫码电脑也绑定一个仓库和一名库管，跨仓扫码会被拒绝。

## 部署步骤

1. 服务端执行 `alembic upgrade head`，重启后端并重新构建前端。
2. 系统管理员进入“打印设备认证码”页面，在“仓库扫码设备”中选择仓库和库管，生成一次性显示的认证码。
3. 仓库电脑运行 `scripts/start_warehouse_scanner.bat`。
4. 配置 HTTPS 服务器地址、设备认证码、高拍仪照片保存目录和照片有效分钟数，保存配置。
5. 在扫描枪配置工具中选择 USB-HID 键盘输出，并设置回车后缀。
6. 先扫描置货码；置货完成并拍摄发货/物流凭证后，再扫描发货码。

## 接口

设备请求统一使用请求头 `X-Warehouse-Device-Token`。认证码由管理员生成，只存储在仓库电脑的 Windows DPAPI 加密配置中。

- `POST /api/warehouse-scanning/device/resolve`：读取条码、校验仓库和下一步动作。
- `POST /api/warehouse-scanning/device/placement/confirm`：整单全量置货，需要幂等键。
- `POST /api/warehouse-scanning/device/shipment-proofs`：上传 JPG/PNG 发货凭证。
- `POST /api/warehouse-scanning/device/shipment/confirm`：凭有效照片整单全量发货，需要幂等键。
- `POST /api/warehouse-scanning/device/heartbeat`：设备连通性检查。

业务扫码事件永久保留用于库存审计；设备接口安全日志只保留三天。重复请求使用同一个幂等键时不会重复置货或扣减库存。

## 操作安全

- 发货照片不能跨订单、跨设备或重复使用；相同文件哈希会被服务器拒绝。
- 客户端默认只选择最近十分钟的照片，可在 1 至 60 分钟范围内调整。
- 服务器只接受最近 24 小时拍摄的图片，已上传凭证须在 30 分钟内完成发货。
- 系统管理员可随时停用设备认证码，并查看设备、IP、调用路径及认证结果。
