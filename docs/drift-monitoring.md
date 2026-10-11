# 特征与预测漂移监控

漂移报告比较参考窗口和候选窗口的特征快照与批量预测。十个模型特征分别计算 Population Stability Index（PSI）；预测侧比较平均流失概率和预测正例率的绝对变化。阈值由 `config/drift-policy.json` 管理。

```powershell
monitor-churn-drift `
  --reference-features build/reference/features.ndjson `
  --candidate-features build/candidate/features.ndjson `
  --reference-predictions build/reference/predictions.ndjson `
  --candidate-predictions build/candidate/predictions.ndjson `
  --policy config/drift-policy.json `
  --output build/drift/report.json
```

报告状态为 `PASS` 或 `ALERT`。每项告警具有稳定编号，便于接入调度系统和告警路由。输入血缘使用规范化内容 SHA-256 表示，报告仅包含样本量与聚合统计，不保存匿名客户键或逐客户预测。

PSI 和预测分布变化只能说明输入或模型输出发生偏移，不能直接证明模型效果下降。上线环境仍需等待真实标签成熟后补充性能衰减监控，并根据业务成本审批阈值。
