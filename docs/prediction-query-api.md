# 预测与留存运营 API

查询服务在启动阶段一次性加载模型和预测批次，验证模型特征契约、预测字段、唯一编号、客户快照唯一性和模型 SHA-256 血缘。任一约束不满足时服务拒绝启动，不对外提供部分数据。

先生成演示产物，再启动服务：

```powershell
./.venv/Scripts/run-churn-demo.exe --output-dir build/demo
./scripts/api-up.ps1
```

默认监听 `127.0.0.1:8000`，提供以下接口：

- `GET /health`：服务状态、模型指纹和预测数量；
- `GET /v1/model`：模型版本、算法、特征顺序、阈值和训练数据指纹；
- `GET /v1/predictions`：带 `offset`、`limit` 和预测结果过滤的有界列表；
- `GET /v1/predictions/{prediction_id}`：按稳定预测编号查询；
- `GET /v1/customers/{customer_key}/predictions`：查询匿名客户键的最近预测。
- `GET /v1/portfolio/summary`：查询客户组合、风险分层和运营任务统计；
- `GET /v1/portfolio/priority`：按风险等级筛选客户优先队列；
- `GET /v1/retention/tasks`：查询留存任务；
- `POST /v1/retention/tasks`：为指定预测创建留存任务；
- `PATCH /v1/retention/tasks/{task_id}/status`：更新任务状态。

预测与优先队列单次最多返回 100 行，任务列表单次最多返回 500 行。未知预测、客户或任务返回 404；同一预测重复创建任务返回 409。接口只读取离线预测批次，不在线计算特征或重新执行模型。运营任务使用 SQLite 保存，适用于单实例运行；生产部署仍需补充身份认证、租户隔离和高可用存储。

