# 一键建模与监控流程

`scripts/quickstart.py` 从固定种子的匿名事件开始，依次构建六期时间点特征、训练 Champion 与 Challenger、执行批量推理、计算漂移和延迟标签效果，并输出模型对比建议。

## 运行

```shell
python -m pip install -e .
python scripts/quickstart.py
```

入口只要求核心项目环境；查询 API 是独立可选能力，不是完成建模流程的前置条件。输出目录可通过参数修改：

```shell
python scripts/quickstart.py --output-dir build/local-run
```

脚本使用当前 Python 解释器，并从脚本位置解析仓库根目录，可从任意工作目录调用。所有输入均由确定性生成器产生，重复运行会覆盖同一组可再生产物。

## 输出契约

默认输出位于 `build/quickstart/`：

- `doctor.json` 和 `doctor.md`：核心运行条件诊断；
- `demo/events/` 与 `demo/snapshots/`：匿名事件和时间点特征；
- `demo/model/` 与 `demo/predictions/`：模型、指标、预测批次和血缘清单；
- `demo/drift/`、`demo/performance/`、`demo/comparison/`：监控与模型对比报告；
- `demo/summary.json`：底层演示契约；
- `summary.json`：项目版本、关键状态、模型指纹和产物位置。

顶层摘要仅在底层演示状态为 `COMPLETE` 时写入。漂移状态允许为 `ALERT`，它表示候选批次触发预设阈值，不表示运行中断或模型效果已经下降。
