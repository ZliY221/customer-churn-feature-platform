# 用户行为特征平台与客户流失预测系统

本项目实现从匿名行为事件、时间点特征快照、模型训练与评估，到批量推理、查询服务和漂移监控的完整机器学习数据链路。设计重点是防止标签泄漏、保证训练可复现，并让数据版本、模型版本和预测结果能够追溯。

## 当前进度

- [x] 定义客户行为事件 v1 契约。
- [x] 实现固定种子的匿名行为事件生成器。
- [x] 实现无第三方依赖的事件校验器。
- [x] 验证事件确定性、唯一性、时间边界、金额口径与隐私字段。
- [ ] 构建带 `as_of_time` 的离线特征快照。
- [ ] 定义未来 30 天流失标签并加入泄漏检查。
- [ ] 实现按时间切分的训练、验证和测试数据集。
- [ ] 训练基线模型并输出分类评估与校准报告。
- [ ] 保存实验清单、数据指纹与模型哈希。
- [ ] 实现幂等批量推理和 FastAPI 查询服务。
- [ ] 实现数据漂移与预测漂移报告。

## 事件模型

事件包含 `login`、`product_view`、`cart_add`、`order_completed` 和 `support_ticket` 五类行为。每条记录使用匿名 `customer_key`，保留事件时间、摄取时间、渠道、设备、商品类别和可选成交金额，不包含任何真实个人信息。

契约位于 `schemas/customer-behavior-event-v1.schema.json`。金额以两位小数字符串传输，只允许出现在成交事件中。

## 本地运行

要求 Python 3.11 或更高版本。

```powershell
$env:PYTHONPATH = "src"
python -m churn_platform.cli `
  --customers 100 `
  --days 60 `
  --seed 2027 `
  --output build/events/customer_events.ndjson
```

运行测试：

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

## 设计边界

当前版本只完成事件数据基础，不包含模型效果、线上服务或生产吞吐结论。后续版本将按照 [架构设计](docs/architecture.md) 逐步增加特征、训练、推理和监控能力，每项能力均配套自动化测试和机器可读证据。

## 文档

- [需求与验收标准](docs/requirements.md)
- [架构设计](docs/architecture.md)
