# 本地验收回执

记录本次本地实际执行结果。回执只针对脱敏离线 Demo；不代表原生产系统当前状态，也不表示真实微信用户收件。

## Python 3.12

运行环境：`Python 3.12.10`。

命令：`python -m telegram_ai_monitor_demo demo`

结果：退出码 `0`。真实标准输出显示：5 条合成消息、筛选 2 条、写入 3 条告警；假接口返回 `business_code: -2` 后 SQLite 为 `circuit_open=true`、日报 `send_failed=1`、告警 `queued=3`；同一版本由新进程启动时 `requests=0`，本地累计请求仍为 1；版本变化后恢复阶段 `requests=2`（已存日报 + 1 条告警），日报与 1 条告警变为 sent；30 秒时为 `alert_throttled`；最终 3 条告警和 1 份日报均为 sent，且输出 `temporary_state_cleaned=True`。

命令：`python -m unittest discover -s tests -v`

实际结果：6 项测试全部 `ok`，`Ran 6 tests in 6.930s`，`OK`，退出码 `0`。测试覆盖元数据只读、跨进程熔断与日报复用、模拟时间限速、Demo 清理、本地 endpoint 限制、并发恢复和公开树审计。

命令：`python -m compileall -q telegram_ai_monitor_demo tests scripts`

实际结果：无错误输出，退出码 `0`。

## Python 3.11

命令：`py -3.11 -m telegram_ai_monitor_demo demo`

结果：退出码 `0`；同样观察到 -2 持久熔断、同版本重启零请求、版本变化后 2 次恢复请求、模拟 60 秒限速和临时状态清理。

命令：`py -3.11 -m unittest discover -s tests -v`

实际结果：6 项测试全部 `ok`，`Ran 6 tests in 6.738s`，`OK`，退出码 `0`。

## 回执边界

本文件记录的是发布前在本地执行的 Demo、测试、编译和白名单检查结果；这些结果不代替 GitHub Actions。公开仓库的远端运行状态应以仓库的 Actions 页面为准。
