# 用户行为特征平台与客户流失预测系统

本项目实现从匿名行为事件、时间点特征快照、模型训练与评估，到批量推理、查询服务和漂移监控的完整机器学习数据链路。设计重点是防止标签泄漏、保证训练可复现，并让数据版本、模型版本和预测结果能够追溯。

当前版本：`v0.9.0`。版本变更见 [CHANGELOG](CHANGELOG.md)，版本摘要见 [VERSION_LOG](VERSION_LOG.md)。

## 当前进度

- [x] 定义客户行为事件 v1 契约。
- [x] 实现固定种子的匿名行为事件生成器。
- [x] 实现无第三方依赖的事件校验器。
- [x] 验证事件确定性、唯一性、时间边界、金额口径与隐私字段。
- [x] 构建带 `as_of_time` 的离线特征快照。
- [x] 定义未来 30 天流失标签并加入泄漏检查。
- [x] 实现按时间切分的训练、验证和测试数据集。
- [x] 训练确定性逻辑回归基线并输出分类评估与校准指标。
- [x] 保存特征清单、训练数据指纹、标准化参数和模型权重。
- [x] 实现带模型与数据血缘的幂等批量推理。
- [x] 实现 FastAPI 预测查询服务。
- [x] 实现带输入血缘、PSI 和稳定告警编号的数据漂移与预测漂移报告。
- [x] 实现延迟标签覆盖检查与模型效果监控。
- [x] 实现同批成熟标签下的 Champion/Challenger 模型对比与晋级建议。
- [x] 提供核心链路与 API 两档跨平台环境自检和诊断报告。

## 事件模型

事件包含 `login`、`product_view`、`cart_add`、`order_completed` 和 `support_ticket` 五类行为。每条记录使用匿名 `customer_key`，保留事件时间、摄取时间、渠道、设备、商品类别和可选成交金额，不包含任何真实个人信息。

契约位于 `schemas/customer-behavior-event-v1.schema.json`。金额以两位小数字符串传输，只允许出现在成交事件中。

## 本地运行

要求 Python 3.11 或更高版本。

克隆或下载仓库后，可先检查核心链路或 API 运行条件：

```powershell
python scripts/project_doctor.py --profile core
python scripts/project_doctor.py --profile api --json-output build/doctor/report.json --markdown-output build/doctor/report.md
```

自检入口只使用 Python 标准库；详细范围和退出码见[环境自检](docs/environment-preflight.md)。

```powershell
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -e ".[api,test]"
./.venv/Scripts/run-churn-demo.exe --output-dir build/demo
```

该命令从固定种子的匿名事件开始，依次生成六个时间点特征快照，完成时间切分训练、Champion 与 Challenger 推理、漂移报告、延迟标签效果报告和模型对比建议。入口正常结束后会生成 `build/demo/summary.json`；模型指标、预测清单、输入指纹和监控明细保存在同一目录下。监控状态可能为 `ALERT`，表示候选批次触发策略阈值，不代表演示流程执行失败。

各阶段也可通过安装后生成的 `generate-customer-events`、`build-customer-features`、`train-churn-baseline`、`predict-customer-churn`、`monitor-churn-drift`、`monitor-churn-performance` 和 `compare-churn-models` 命令独立运行。

生成演示产物后可启动只读预测查询服务：

```powershell
./scripts/api-up.ps1
```

服务默认读取 `build/demo/model/baseline.json` 和 `build/demo/predictions/candidate.ndjson`，接口说明见 [预测查询 API](docs/prediction-query-api.md)。

运行测试：

```powershell
./.venv/Scripts/python.exe -m unittest discover -s tests -v
```

## 设计边界

当前版本完成事件数据基础、时间点特征快照、未来流失标签、按时间切分的逻辑回归基线和幂等批量推理。分类阈值只在验证集选择，测试集仅做最终评估；预测记录使用模型哈希、客户键和快照时间生成稳定 ID，清单保存输入、模型和输出 SHA-256。仓库中的自动化测试数据用于验证实现，不作为业务模型效果声明。

漂移报告按特征输出 PSI，并检查平均预测概率和正例率变化。报告只保存聚合结果和规范化输入指纹；漂移告警不等同于模型效果衰减结论。

延迟标签效果监控要求观察时间覆盖完整的未来 30 天，并校验标签覆盖率、已标注行数和类别完整性。数据充分后才计算 ROC-AUC、PR-AUC、F1 和 Brier 分数；报告不保存客户级关联结果。

## 文档

- [需求与验收标准](docs/requirements.md)
- [跨平台环境自检](docs/environment-preflight.md)
- [架构设计](docs/architecture.md)
- [特征与预测漂移监控](docs/drift-monitoring.md)
- [预测查询 API](docs/prediction-query-api.md)
- [延迟标签模型效果监控](docs/model-performance-monitoring.md)
- [Champion/Challenger 模型对比](docs/model-comparison.md)
- [版本日志](VERSION_LOG.md)
