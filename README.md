# EV Dynamic Dispatch

一个面向电动车配送场景的动态调度仿真仓库。

仓库当前已经具备完整的基础闭环：

- 路网建模与最短路计算
- 动态任务生成
- 车辆电量、载重、体积、货物类型约束
- 充电站排队与充电过程
- 多种调度策略对比
- Matplotlib 动画可视化
- Streamlit 仪表盘可视化
- 多场景、多随机种子的 benchmark 实验

## 项目概览

这个项目模拟的是一个城市内的新能源配送车队。系统会在离散时间步中不断生成新任务，然后由调度算法为车辆分配任务或安排充电，最后统计任务完成率、成本、路程、评分等指标。

当前代码更像一个“可扩展的实验平台”，而不只是一个一次性 demo。你可以直接跑可视化，也可以把它当作调度算法测试床，继续加启发式、精确求解或学习型策略。

## 当前能力

### 1. 路网与仿真

- `RoadNetwork`：
  使用网格路网和 `networkx` 建图，支持最短路距离和拥堵影响下的出行时间估计。道路边包含道路等级、车道数、限速、路面质量、坡度、交叉口密度、事故风险、货车限制和收费等属性，这些属性会影响车辆耗时、能耗和运输成本。
- `Simulator`：
  负责生成任务、推进充电站状态、调用调度器、执行动作、记录结果和轨迹帧。
- `Task`：
  包含起终点、重量、体积、deadline、货物类型、完成时间、失败时间等信息。
- `Vehicle`：
  包含车辆位置、电量、载重、体积、支持货物类型、充电状态等。
- `ChargingStation`：
  支持多充电桩、FIFO 排队、等待时间估计和充电过程推进。

### 2. 已实现的调度策略

当前仓库内置三种策略：

- `nearest`
  最近任务优先，偏简单贪心。
- `largest`
  最大任务优先，优先处理重量较大的任务。
- `composite`
  综合评分策略，会同时考虑距离、时间、deadline 风险、能耗、充电可达性、容量匹配和货物类型约束。

策略实现位置：

- [strategies.py](/c:/Users/lhxsy/OneDrive/EV-Dynamic-Dispatch/ev_dispatch/algorithms/strategies.py)

### 3. 结果指标

仿真当前会输出一组适合做实验比较的指标，包括：

- `completed`
- `failed`
- `generated`
- `pending`
- `completion_rate`
- `failure_rate`
- `on_time_rate`
- `avg_delay_hours`
- `avg_service_hours`
- `total_distance`
- `total_time_hours`
- `total_cost`
- `total_score`
- `distance_per_completed_task`
- `cost_per_completed_task`
- `avg_battery_ratio`
- `avg_station_wait_minutes`

## 安装依赖

```bash
pip install -r requirements.txt
```

当前依赖很轻：

```text
numpy
networkx
matplotlib
streamlit
```

如果你的机器上有多个 Python 环境，建议始终用同一个解释器安装和运行。例如：

```powershell
C:\Users\lhxsy\anaconda3\python.exe -m pip install -r requirements.txt
```

## 快速开始

### 1. 运行命令行 demo

```bash
python -m ev_dispatch.main
```

这个入口会：

- 创建默认场景
- 分别运行 `nearest`、`largest`、`composite`
- 打印每种策略的结果摘要
- 输出最终对比

### 2. 启动 Matplotlib 动画

```bash
python -m ev_dispatch.main --visualize --steps 20 --tasks-per-step 3
```

如果只想播放某条策略轨迹：

```bash
python -m ev_dispatch.main --visualize --visualize-strategy composite
```

可选值：

- `nearest`
- `largest`
- `composite`

导出 GIF：

```bash
python -m ev_dispatch.main --visualize --visualize-strategy composite --save-animation outputs/demo.gif --no-show
```

### 3. 启动 Streamlit 仪表盘

```bash
streamlit run ev_dispatch/visualization/streamlit_dashboard.py
```

如果 `streamlit` 命令不可用：

```bash
python -m streamlit run ev_dispatch/visualization/streamlit_dashboard.py
```

启动后在浏览器打开本地地址，通常是：

```text
http://localhost:8501
```

当前 Streamlit 页面支持：

- 场景规模选择
- 仿真步数和任务数设置
- 随机种子设置
- 最近优先 / 最大优先 / 综合评分 / 三策略对比
- 任务趋势图
- 平均电量趋势图
- 路网快照与轨迹播放

Streamlit 仪表盘已经接入 `composite` 策略，可直接在算法选择中运行单策略或三策略对比。

更详细的可视化说明见：

- [可视化仿真使用教程.md](</c:/Users/lhxsy/OneDrive/EV-Dynamic-Dispatch/docs/可视化仿真使用教程.md>)

## 运行 benchmark

仓库提供了一个可复现实验入口：

```bash
python -m ev_dispatch.benchmark
```

默认会运行：

- `small_city`
- `nearest,largest,composite`
- `42,43,44` 三个随机种子

自定义示例：

```bash
python -m ev_dispatch.benchmark --scales small_city,medium_city --strategies nearest,composite --seeds 42,43 --steps 24 --tasks-per-step 4 --tag my_run
```

`composite` 支持预设权重变体，可用于快速调参对比：

```bash
python -m ev_dispatch.benchmark --strategies composite,composite:deadline,composite:cost,composite:energy --seeds 42,43 --steps 12 --tasks-per-step 3 --tag composite_tuning
```

当前内置预设：

- `composite` 或 `composite:balanced`
- `composite:deadline`
- `composite:cost`
- `composite:energy`

输出文件位于 `outputs/benchmarks/`：

- `runs_<tag>.csv`
- `summary_<tag>.csv`
- `benchmark_<tag>.json`

这部分代码在：

- [benchmark.py](/c:/Users/lhxsy/OneDrive/EV-Dynamic-Dispatch/ev_dispatch/benchmark.py)

### 自动调优综合策略参数

可以用多随机种子反复评估 `DispatcherCompositeScore` 的权重，搜索平均评分更高的参数组合：

```bash
python -m ev_dispatch.tune_composite --scales small_city --seeds 42,43,44,45 --trials 40 --steps 12 --tasks-per-step 3 --tag small_tuned
```

输出文件位于 `outputs/tuning/`：

- `composite_tuning_<tag>.json`
- `composite_tuning_<tag>.csv`

如果希望参数在不同种子下更稳，可以加入标准差惩罚：

```bash
python -m ev_dispatch.tune_composite --seeds 42,43,44,45,46 --trials 80 --stdev-penalty 0.2 --tag robust
```

## 场景配置

默认提供四档城市规模：

- `small_city`
- `medium_city`
- `large_city`
- `mega_city`

定义位置：

- [city_scales.py](/c:/Users/lhxsy/OneDrive/EV-Dynamic-Dispatch/ev_dispatch/scenarios/city_scales.py)

默认场景构建逻辑在：

- [default.py](/c:/Users/lhxsy/OneDrive/EV-Dynamic-Dispatch/ev_dispatch/scenarios/default.py)

## 目录结构

```text
ev_dispatch/
├── algorithms/      # 调度策略与接口
├── core/            # 路网、车辆、任务、能耗、充电等核心模型
├── scenarios/       # 默认场景与城市规模配置
├── simulator/       # 主仿真循环
├── visualization/   # Matplotlib 与 Streamlit 可视化
├── benchmark.py     # 批量实验入口
└── main.py          # demo 与动画入口
```

`docs/` 目录下放的是补充说明文档，包括可视化教程和一些项目分析材料。

## 适合继续扩展的方向

如果你打算继续把这个仓库往“算法项目”推进，比较自然的方向有：

- 基于 benchmark 输出继续扩大 `composite` 权重搜索范围
- 增加更细粒度的可视化状态，例如车辆在途、充电、空闲的图例和筛选
- 增加滚动规划或插入式路径构造
- 引入更严格的时间窗 / 服务时间建模
- 增加 RL 或精确求解器对照实验

## 相关入口文件

- [main.py](/c:/Users/lhxsy/OneDrive/EV-Dynamic-Dispatch/ev_dispatch/main.py)
- [benchmark.py](/c:/Users/lhxsy/OneDrive/EV-Dynamic-Dispatch/ev_dispatch/benchmark.py)
- [simulator.py](/c:/Users/lhxsy/OneDrive/EV-Dynamic-Dispatch/ev_dispatch/simulator/simulator.py)
- [strategies.py](/c:/Users/lhxsy/OneDrive/EV-Dynamic-Dispatch/ev_dispatch/algorithms/strategies.py)
- [streamlit_dashboard.py](/c:/Users/lhxsy/OneDrive/EV-Dynamic-Dispatch/ev_dispatch/visualization/streamlit_dashboard.py)
- [matplotlib_player.py](/c:/Users/lhxsy/OneDrive/EV-Dynamic-Dispatch/ev_dispatch/visualization/matplotlib_player.py)
