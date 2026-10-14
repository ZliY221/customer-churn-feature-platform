# 运行环境分层

项目使用两类轻量 Python 环境，不要求安装外部数据库。

| 环境 | 适用场景 | 主要依赖 | 检查命令 |
| --- | --- | --- | --- |
| 建模环境 | 特征、训练、推理与监控 | Python 3.11+ | `python scripts/project_doctor.py --profile core` |
| 运营环境 | 建模链路、FastAPI 与 SQLite 任务库 | Python 3.11+、API 依赖 | `python scripts/project_doctor.py --profile api` |

公开启动器在项目外的受管缓存目录创建专属虚拟环境，避免多个仓库共享依赖，并固定 UTF-8 子进程输出。自动化环境覆盖 Linux Python 3.11/3.13、Windows Python 3.11 和 macOS Python 3.11。

批量建模与监控使用建模环境；需要运营工作台和任务接口时使用运营环境。
