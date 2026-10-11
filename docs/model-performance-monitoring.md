# 延迟标签模型效果监控

批量预测完成时，未来 30 天流失标签尚未成熟，因此数据漂移和模型效果需要分开处理。`monitor-churn-performance` 在观察窗口结束后，将不可变预测批次与标签批次按 `(customer_key, as_of_time)` 精确关联，生成模型效果报告。

标签使用 NDJSON，每行包含匿名客户键、预测快照时间、布尔型 `churned_30d` 和带时区的 `label_observed_at`。观察时间必须覆盖快照后的完整 30 天，重复身份会被拒绝。预测批次必须只包含一个模型 SHA-256 和一个决策阈值，声明的分类结果也必须与概率及阈值一致。

```powershell
monitor-churn-performance `
  --predictions build/demo/predictions/candidate.ndjson `
  --labels build/demo/labels/candidate.ndjson `
  --policy config/performance-policy.json `
  --output build/demo/performance/report.json
```

报告先检查标签覆盖率、已标注行数和类别完整性。数据不足时状态为 `INSUFFICIENT_DATA`，不计算容易误导的分类指标；达到门槛后输出 ROC-AUC、PR-AUC、F1、Brier 分数和混淆矩阵，并按策略产生稳定告警编号。报告只包含聚合统计、数据指纹和模型指纹，不保存客户级关联结果。
