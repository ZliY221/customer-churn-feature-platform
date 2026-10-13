# 用户使用指南

适用版本：`v1.3.0`

本指南随项目版本同步更新，覆盖当前版本的数据链路、模型监控和留存运营流程。

## 1. 环境要求

- Python 3.11 或更高版本
- 本地完整演示无需外部数据库，运营任务使用 SQLite 保存

## 2. 最快启动

在仓库根目录执行：

```powershell
python scripts/bootstrap.py
```

该入口会创建隔离运行时，生成匿名行为事件，构建时间点特征，训练基线与挑战者模型，运行批量推理、漂移监控、性能监控和模型比较。输出位于 `build/quickstart/`。

## 3. 启动运营工作台

先完成最快启动，再执行：

```powershell
./scripts/api-up.ps1
```

访问 `http://127.0.0.1:8000/console`。工作台提供客户组合概览、风险分层、高风险优先队列和留存任务看板。

## 4. 留存任务

在优先队列中选择客户并创建任务，填写负责人和执行策略。任务支持以下状态：

- `OPEN`：待执行
- `IN_PROGRESS`：处理中
- `COMPLETED`：已完成
- `DISMISSED`：已关闭

每次创建和状态变更都会写入独立事件流水。工作台显示累计变更记录数；点击任务卡片中的“查看时间线”可以直接查看创建时间和历次状态流转。任务看板支持按风险等级和负责人组合筛选。接口 `GET /v1/retention/tasks/{task_id}/events` 可供外部系统查询同一时间线。

## 5. 主要接口

- `GET /health`：模型与预测批次就绪状态。
- `GET /v1/portfolio/summary`：客户组合、风险与任务汇总。
- `GET /v1/portfolio/priority`：按风险等级读取运营优先队列。
- `POST /v1/retention/tasks`：创建留存任务。
- `GET /v1/retention/tasks?risk_band=HIGH&owner=retention-team`：按风险与负责人筛选任务。
- `PATCH /v1/retention/tasks/{task_id}/status`：更新任务状态。
- `GET /v1/retention/tasks/{task_id}/events`：查询任务事件流水。

接口文档位于 `http://127.0.0.1:8000/docs`。

## 6. 模型与数据监控

完整演示会输出特征漂移、预测分布漂移、延迟标签效果和 Champion/Challenger 对比报告。报告只保存聚合指标、模型摘要和匿名键，不输出客户直接身份信息。

## 7. 常见问题

- API 提示模型文件不存在：先执行 `python scripts/bootstrap.py`。
- 任务数据库需要切换位置：通过 `CHURN_TASK_DATABASE_PATH` 指定 SQLite 文件。
- 重复创建任务返回冲突：同一预测只允许一个留存任务，可在任务看板继续更新状态。
- 页面没有最新数据：点击刷新，或检查 `/health` 与浏览器网络请求。

## 8. 数据边界

仓库样例全部为匿名合成数据。特征按 `as_of_time` 构建，未来事件只用于生成延迟标签，不进入当期特征。模型文件、预测批次和任务库均保留可追溯标识。
