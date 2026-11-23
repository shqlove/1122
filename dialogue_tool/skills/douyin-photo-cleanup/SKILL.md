---
name: douyin-photo-cleanup
description: 在工作台的技能大全中，一键查找已核验发布的抖音作品对应的手机相册原片，只将逐一匹配的源视频移入 iPhone「最近删除」。
---

# 清理已发布原片

从「技能大全 → 清理已发布原片」启动。工作台从已核验发布的记录自动找出当前已绑定手机上的候选，逐条核对。用户无需选择记录。普通图库里仍待发的视频和其他照片都应保留。

1. 候选必须有 `published` 记录、同一手机的固定绑定、已记录的 USB 文件名和 SHA-256，以及能精确匹配的相册显示名。审核中、提交结果不明、设备离线或身份校验不足的候选分别说明原因。此前的删除结果待核查时，先复查图库。
2. 用 `phone_cleanup_open()` 打开系统「照片」。用 `phone_screen()` 获取每次操作所用的最新画面和 `observation_id`。导航到候选的单个视频，暂停播放并打开信息页，读取完整相册显示名（包括 Panda Helper 导入名中的 UUID）。用 `phone_cleanup_verify(filename, observation_id)` 同时核对名称和记录对应的 USB 文件哈希。相似封面、相册中最新的位置、抖音另存的副本或仍能通过 USB 读取的「最近删除」文件都不能代替这一核对。
3. 核对通过后关闭当前视频的信息页一次，停在同一单个视频画面。重新截图，用 `phone_cleanup_trash(observation_id, x, y)` 点击它的垃圾桶。再截图核对系统确认框只删除当前这一个视频，使用 `phone_cleanup_delete(observation_id, x, y)` 单次确认。点击坐标按实际完整截图换算为 0–100。普通手势用于导航和复查。流程止于移入「最近删除」。
4. 返回普通图库复查。iOS 可能自动打开相邻视频或显示「最近删除」说明，按最新画面返回图库。核对目标已消失，能读到计数时核对变化。用 `phone_cleanup_record(status="deleted", detail=..., observation_id=...)` 保存实际证据。无法核实时用 `status="blocked"` 记录原因。已发出删除而结果不明时先做只读复查，避免重按删除。最终按手机汇报已移入「最近删除」、待核查和阻塞的记录。

## 独立运行

从工作台项目根目录使用 `.venv\Scripts\python.exe` 和 `dialogue_tool.settings.settings()`。用同一 `data` 创建 `dialogue_tool.douyin_publish.DouyinPublish`。用户明确发起清理后，由 `publisher.begin_auto_cleanup()` 自动建立批次，再调用 `dialogue_tool.douyin_agent.run_batch(publisher, batch, codex)`，其中 `codex` 来自工作台配置。执行器按记录逐条锁定目标，从 `publisher.cleanup_state()` 读取本轮结果。

[效卫设备截图](https://www.xiaowei.xin/ios-sign/support/834) · [效卫屏幕控制](https://www.xiaowei.xin/ios-sign/support/819) · [Apple 照片删除与同步说明](https://support.apple.com/zh-cn/104967)
