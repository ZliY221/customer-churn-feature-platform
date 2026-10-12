# Champion/Challenger 模型对比

`compare-churn-models` 使用同一批成熟标签比较当前 Champion 与候选 Challenger。两个预测批次必须包含完全相同的 `(customer_key, as_of_time)` 身份集合，并分别关联不同的模型 SHA-256；身份缺失、重复或错位时直接终止，避免样本差异被误判为模型收益。

```powershell
compare-churn-models `
  --champion-predictions build/demo/predictions/candidate.ndjson `
  --challenger-predictions build/demo/predictions/challenger.ndjson `
  --labels build/demo/labels/candidate.ndjson `
  --performance-policy config/performance-policy.json `
  --comparison-policy config/model-comparison-policy.json `
  --output build/demo/comparison/report.json
```

对比先复用延迟标签效果监控的覆盖率、样本量、类别完整性和绝对效果策略。任一批次数据不足时返回 `DEFER_DECISION`；数据充分后计算 Challenger 相对 Champion 的 ROC-AUC、PR-AUC、F1 和 Brier 分数变化。只有所有改进门槛均满足且 Challenger 通过绝对效果策略时，建议才为 `PROMOTE_CHALLENGER`，否则保持 `KEEP_CHAMPION`。

报告只保存聚合指标、模型指纹、输入指纹和规则判断，不保存客户级关联结果。建议结果是可审计的模型选择证据，不直接替换线上模型文件或服务配置。
