# 跨平台环境自检

`scripts/project_doctor.py` 只依赖 Python 标准库，可在执行演示管道或启动 API 前确认仓库是否具备所需条件。

## 检查范围

| Profile | 检查内容 | 适用入口 |
|---|---|---|
| `core` | Python 3.11、固定样例、三类监控策略、`build/` 写权限 | 特征、训练、推理与模型监控 |
| `api` | `core` 加 FastAPI、Uvicorn | 预测查询服务 |

```shell
python scripts/project_doctor.py --profile api --json-output build/doctor/report.json --markdown-output build/doctor/report.md
```

全部条件满足时报告状态为 `READY`，进程退出码为 `0`；缺少必需条件时状态为 `BLOCKED`，退出码为 `1`。报告只记录运行时版本与依赖可用性，不读取模型权重、客户级预测内容或环境变量值。

## 平台验证范围

`portable-smoke.yml` 在 Windows、Ubuntu 和 macOS 上执行 Python 3.11 安装、API 环境自检和完整确定性演示流程，并重新核对演示产物契约。
