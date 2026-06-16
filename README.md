# 本地量化分析框架

这是一个本地化 A 股量化研究与回测框架。v1 目标不是追求超额收益，而是用经典、透明的策略跑通数据获取、缓存、策略、回测、绩效分析和测试闭环。

## 环境

当前项目按本机现有 Python 版本开发：

```bash
python3 --version
```

建议创建虚拟环境：

macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Windows PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

## 快速开始

下载 A 股日线数据：

```bash
quant-framework download --symbol 000001 --start-date 2020-01-01 --end-date 2024-12-31
```

运行均线交叉策略回测：

```bash
quant-framework backtest --symbol 000001 --start-date 2020-01-01 --end-date 2024-12-31
```

运行其他内置策略：

```bash
quant-framework backtest --strategy dual_thrust --symbol 000001 --start-date 2020-01-01 --end-date 2024-12-31
quant-framework backtest --strategy boll_breakout --symbol 000001 --start-date 2020-01-01 --end-date 2024-12-31
quant-framework backtest --strategy turtle --symbol 000001 --start-date 2020-01-01 --end-date 2024-12-31
```

也可以直接运行示例：

```bash
python examples/run_ma_cross.py
```

## 项目结构

```text
src/quant_framework/
  analysis/      绩效指标与后续统计验证接口
  backtest/      backtrader 数据适配与回测运行器
  cli/           命令行入口
  data/          akshare 数据源与 Parquet 缓存
  strategies/    经典策略库
configs/         示例配置
data/            本地数据缓存
examples/        可运行样例
tests/           自动化测试
```

## v1 范围

- 单标的 A 股日线回测。
- 默认前复权日线数据。
- Parquet 本地缓存。
- backtrader 作为默认回测引擎。
- 内置策略包括均线交叉、Dual Thrust、布林突破、海龟交易。
- 对交易收益率序列提供 T 检验和 Bootstrap 置信区间验证。

## 注意

量化生态库对最新 Python 的支持可能会晚于 Python 发布节奏。如果安装依赖遇到兼容问题，可以先固定到 Python 3.11；本项目的内部接口会尽量保持版本无关。
