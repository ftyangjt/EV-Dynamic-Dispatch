# 综合策略调参与 Benchmark 使用指南

本文档说明如何使用项目中的 `benchmark.py` 和 `tune_composite.py` 做策略对比、综合评分策略调参和结果分析。

## 1. 准备环境

在项目根目录运行：

```powershell
pip install -r requirements.txt
```

如果你的机器上有多个 Python 环境，建议明确使用同一个解释器，例如：

```powershell
python -m pip install -r requirements.txt
```

后续命令都建议在项目根目录执行。

## 2. Benchmark 是做什么的

`ev_dispatch/benchmark.py` 用来批量运行仿真实验，并把不同策略放在相同场景、相同随机种子下比较。

它默认会运行：

- 场景：`small_city`
- 策略：`nearest,largest,composite`
- 随机种子：`42,43,44`
- 仿真步数和每步任务数：使用场景默认配置

默认命令：

```powershell
python -m ev_dispatch.benchmark
```

运行后会输出三个文件：

```text
outputs/benchmarks/runs_latest.csv
outputs/benchmarks/summary_latest.csv
outputs/benchmarks/benchmark_latest.json
```

其中最常用的是：

- `runs_*.csv`：每一次单独运行的原始结果。
- `summary_*.csv`：按场景和策略聚合后的平均值、标准差。
- `benchmark_*.json`：包含命令参数、原始结果和汇总结果，适合留档。

## 3. 常用 Benchmark 命令

### 3.1 跑默认三策略对比

```powershell
python -m ev_dispatch.benchmark --tag baseline
```

输出文件会变成：

```text
outputs/benchmarks/runs_baseline.csv
outputs/benchmarks/summary_baseline.csv
outputs/benchmarks/benchmark_baseline.json
```

建议每次实验都加 `--tag`，避免覆盖之前结果。

### 3.2 指定场景规模

项目支持四档 benchmark 场景：

```text
small_city
medium_city
large_city
mega_city
```

示例：

```powershell
python -m ev_dispatch.benchmark --scales small_city,medium_city --tag scale_compare
```

如果只是答辩展示，建议优先跑 `small_city`，速度快、结果稳定。  
如果想证明系统可扩展，可以再补一个 `medium_city`。

### 3.3 指定策略

只比较最近优先和综合评分：

```powershell
python -m ev_dispatch.benchmark --strategies nearest,composite --tag nearest_vs_composite
```

比较综合评分的不同预设：

```powershell
python -m ev_dispatch.benchmark --strategies composite,composite:deadline,composite:cost,composite:energy --tag composite_presets
```

当前支持的综合评分预设：

```text
balanced
deadline
cost
energy
```

其中：

- `composite` 等价于默认的 `balanced`
- `composite:deadline` 更重视截止时间
- `composite:cost` 更重视距离、成本和能耗
- `composite:energy` 更重视低电风险和续航安全

### 3.4 指定随机种子

随机种子用于保证实验可复现。建议不要只看一个种子，至少用 3 到 5 个。

```powershell
python -m ev_dispatch.benchmark --seeds 42,43,44,45,46 --tag five_seeds
```

种子越多，结果越稳，但运行时间也越长。

### 3.5 指定仿真步数和任务密度

```powershell
python -m ev_dispatch.benchmark --steps 24 --tasks-per-step 4 --tag custom_load
```

含义：

- `--steps 24`：仿真 24 个调度小时。
- `--tasks-per-step 4`：每个调度小时生成 4 个任务。

如果想增加压力，可以提高任务密度：

```powershell
python -m ev_dispatch.benchmark --steps 24 --tasks-per-step 6 --tag high_load
```

高负载实验更容易看出策略差异，但也更容易出现任务失败。

## 4. 如何看 Benchmark 结果

重点看 `summary_*.csv`。

常用指标如下：

| 指标 | 含义 | 趋势 |
| --- | --- | --- |
| `completion_rate_mean` | 平均任务完成率 | 越高越好 |
| `on_time_rate_mean` | 平均准时率 | 越高越好 |
| `total_score_mean` | 平均总分 | 越高越好 |
| `cost_per_completed_task_mean` | 单完成任务成本 | 越低越好 |
| `distance_per_completed_task_mean` | 单完成任务里程 | 越低越好 |
| `avg_battery_ratio_mean` | 仿真结束平均电量比例 | 不能过低 |
| `avg_station_wait_minutes_mean` | 平均充电等待时间 | 越低越好 |
| `*_stdev` | 多随机种子下的标准差 | 越低越稳定 |

答辩时建议优先展示这几个：

```text
completion_rate_mean
on_time_rate_mean
cost_per_completed_task_mean
distance_per_completed_task_mean
total_score_mean
```

不要只看 `total_score_mean`。如果某策略总分高，但完成率低或波动大，也不一定是更好的策略。

## 5. 不同地图规模的运行时间

Benchmark 的耗时会随着地图规模、车辆数、任务数、策略数和随机种子数快速增长。尤其是 `composite` 综合评分策略，它会反复评估候选“车辆-任务”组合，还要查询路径、时间、能耗和充电站可达性，所以比简单策略更耗时。

四档 benchmark 场景的默认配置如下：

| 场景 | 节点数 | 车辆数 | 充电站数 | 默认步数 | 每步任务数 | 默认总任务数 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `small_city` | 64 | 12 | 4 | 24 | 4 | 96 |
| `medium_city` | 144 | 32 | 10 | 36 | 11 | 396 |
| `large_city` | 256 | 90 | 24 | 48 | 30 | 1440 |
| `mega_city` | 400 | 220 | 60 | 72 | 72 | 5184 |

在当前机器上，用 `composite`、单个随机种子 `42` 做实测，结果大致如下：

| 场景 | 实测命令规模 | 耗时 |
| --- | --- | ---: |
| `small_city` | 默认配置，24 步，每步 4 任务 | 约 2.3 秒 |
| `medium_city` | 默认配置，36 步，每步 11 任务 | 约 39.5 秒 |
| `large_city` | 默认配置，48 步，每步 30 任务 | 超过 5 分钟，测试中 300 秒超时 |
| `large_city` | 缩减配置，6 步，每步 30 任务 | 约 33.5 秒 |
| `mega_city` | 缩减配置，3 步，每步 72 任务 | 约 84.8 秒 |

注意：上表是单策略、单随机种子的耗时。默认 benchmark 命令会跑 3 个策略和 3 个随机种子，也就是 9 次仿真：

```powershell
python -m ev_dispatch.benchmark
```

如果把完整三策略三种子套到更大地图上，耗时会大幅增加。粗略估计：

| 场景 | 单个 `composite` run | 三策略三种子估计 |
| --- | ---: | ---: |
| `small_city` | 秒级 | 约几十秒 |
| `medium_city` | 约 40 秒 | 约 5 到 8 分钟 |
| `large_city` | 数分钟以上 | 可能 45 分钟以上 |
| `mega_city` | 预计数十分钟以上 | 可能数小时 |

这些估计不是严格线性外推。因为任务会积累，候选任务池会变大，后期调度可能比前期更慢，所以大规模默认配置通常会比简单线性估算更慢。

### 5.1 为什么大地图会变慢

主要原因有三个：

1. 车辆数和任务数同时增加。综合评分策略每轮要扫描大量车辆任务组合。
2. 候选匹配会调用路径估计，包括最短路、行驶时间、道路指标和能耗估计。
3. 综合策略还会考虑完成任务后到充电站的可达性，需要额外评估充电站距离和等待时间。

在 `strategies.py` 中，`composite` 会循环评估可用车辆和候选任务；在 `assignment.py` 中，每个候选组合都会经过容量、货物类型、电量、路径、时间、能耗和成本估计。虽然 `network.py` 已经对最短路和道路指标做了缓存，但大规模场景首次查询仍然很多。

### 5.2 推荐的耗时测试命令

如果想公平比较“地图规模”对耗时的影响，建议固定策略、随机种子、步数和每步任务数：

```powershell
python -m ev_dispatch.benchmark --scales small_city,medium_city,large_city,mega_city --strategies composite --seeds 42 --steps 3 --tasks-per-step 2 --tag time_probe_s3_t2
```

这个命令很快，适合检查程序能否在四种地图上正常启动和运行。

如果想测试默认任务压力，但又不想等太久，可以分地图运行：

```powershell
python -m ev_dispatch.benchmark --scales small_city --strategies composite --seeds 42 --tag time_small
```

```powershell
python -m ev_dispatch.benchmark --scales medium_city --strategies composite --seeds 42 --tag time_medium
```

对于 `large_city` 和 `mega_city`，建议先缩短步数：

```powershell
python -m ev_dispatch.benchmark --scales large_city --strategies composite --seeds 42 --steps 6 --tasks-per-step 30 --tag time_large_probe
```

```powershell
python -m ev_dispatch.benchmark --scales mega_city --strategies composite --seeds 42 --steps 3 --tasks-per-step 72 --tag time_mega_probe
```

### 5.3 答辩和调参时的建议

答辩现场不要临时跑 `large_city` 或 `mega_city` 的完整 benchmark。它们适合作为提前离线实验或压力测试。

推荐现场演示：

```powershell
python -m ev_dispatch.benchmark --scales small_city --strategies nearest,largest,composite --seeds 42,43,44 --tag demo_small
```

推荐调参试跑：

```powershell
python -m ev_dispatch.tune_composite --scales small_city --seeds 42,43 --trials 5 --steps 12 --tasks-per-step 3 --tag quick
```

推荐离线扩展实验：

```powershell
python -m ev_dispatch.benchmark --scales small_city,medium_city --strategies nearest,largest,composite --seeds 42,43,44 --tag offline_scale_compare
```

## 6. 综合评分策略如何调参

综合评分策略在 `ev_dispatch/algorithms/strategies.py` 中定义，核心配置是 `CompositeScoreConfig`。

主要参数含义：

| 参数 | 作用 |
| --- | --- |
| `base_reward` | 每个可行任务的基础收益 |
| `priority_weight` | 任务优先级权重 |
| `urgency_weight` | deadline 紧急程度权重 |
| `load_fit_weight` | 车辆容量匹配奖励 |
| `pickup_distance_weight` | 取货距离惩罚 |
| `delivery_distance_weight` | 配送距离惩罚 |
| `travel_time_weight` | 行驶时间惩罚 |
| `energy_weight` | 能耗惩罚 |
| `lateness_weight` | 延误惩罚 |
| `station_wait_weight` | 充电站等待惩罚 |
| `station_distance_weight` | 完成任务后到充电站距离惩罚 |
| `low_battery_ratio_threshold` | 低电风险阈值 |
| `low_battery_penalty_weight` | 低电风险惩罚 |
| `reserve_energy_kwh` | 完成任务后预留电量 |

综合评分大致是：

```text
任务基础收益
+ 优先级奖励
+ deadline 紧急奖励
+ 容量匹配奖励
- 取货距离惩罚
- 配送距离惩罚
- 时间惩罚
- 能耗惩罚
- 延误惩罚
- 充电等待惩罚
- 到充电站距离惩罚
- 低电风险惩罚
```

同时，代码会把：

```text
任务能耗 + 完成后到充电站能耗 + reserve_energy_kwh
```

作为可行性约束。如果车辆电量不足，这个车辆任务组合会被直接过滤。

## 7. 使用 tune_composite.py 自动调参

`ev_dispatch/tune_composite.py` 会在多个随机种子和场景上反复评估不同权重组合，寻找平均得分更高的参数。

### 7.1 快速试跑

```powershell
python -m ev_dispatch.tune_composite --scales small_city --seeds 42,43 --trials 5 --steps 12 --tasks-per-step 3 --tag quick
```

输出文件：

```text
outputs/tuning/composite_tuning_quick.json
outputs/tuning/composite_tuning_quick.csv
```

### 7.2 正式调参建议命令

```powershell
python -m ev_dispatch.tune_composite --scales small_city --seeds 42,43,44,45,46 --trials 60 --steps 24 --tasks-per-step 4 --stdev-penalty 0.2 --tag small_robust
```

参数说明：

- `--trials 60`：尝试 60 组参数。
- `--seeds 42,43,44,45,46`：用 5 个随机种子评估稳定性。
- `--stdev-penalty 0.2`：对波动大的参数组合进行惩罚，让结果更稳。
- `--tag small_robust`：输出文件标签。

如果机器运行较慢，可以把 `--trials` 降到 20 或 30。

### 7.3 多场景稳健调参

如果希望参数不只适合 `small_city`，可以加入 `medium_city`：

```powershell
python -m ev_dispatch.tune_composite --scales small_city,medium_city --seeds 42,43,44 --trials 40 --stdev-penalty 0.2 --tag multi_scale
```

这会更慢，但得到的参数更有泛化意义。

## 8. 调参后的验证流程

推荐流程是：

1. 先跑默认 benchmark，得到基线。
2. 用 `tune_composite.py` 搜索更好的综合评分参数。
3. 把调参结果中的 `best_config` 复制到 `strategies.py` 里，新增一个预设。
4. 再跑 benchmark，对比调参前后的综合策略。

例如新增预设：

```python
COMPOSITE_CONFIG_PRESETS = {
    "balanced": CompositeScoreConfig(name="balanced"),
    "tuned": CompositeScoreConfig(
        name="tuned",
        urgency_weight=...,
        pickup_distance_weight=...,
        energy_weight=...,
        reserve_energy_kwh=...,
    ),
}
```

然后验证：

```powershell
python -m ev_dispatch.benchmark --strategies nearest,largest,composite,composite:tuned --seeds 42,43,44,45,46 --tag tuned_compare
```

看 `summary_tuned_compare.csv`，重点比较：

```text
composite
composite:tuned
```

如果 `composite:tuned` 的平均总分更高、完成率不下降、标准差更低，就说明调参是有效的。

## 9. 推荐答辩前实验组合

如果时间有限，建议准备三组结果。

### 9.1 基线三策略对比

```powershell
python -m ev_dispatch.benchmark --tag baseline
```

用途：说明项目能比较不同策略。

### 9.2 综合策略预设对比

```powershell
python -m ev_dispatch.benchmark --strategies composite,composite:deadline,composite:cost,composite:energy --seeds 42,43,44 --tag composite_presets
```

用途：说明综合策略可以根据目标偏好调整。

### 9.3 高负载压力测试

```powershell
python -m ev_dispatch.benchmark --strategies nearest,largest,composite --steps 24 --tasks-per-step 6 --seeds 42,43,44 --tag high_load
```

用途：说明任务压力变化时，策略表现会发生差异。

## 10. 调参注意事项

1. 不要只用一个随机种子判断策略好坏。
2. 不要只看总分，要同时看完成率、准时率、成本和波动。
3. 调参时先用小规模快速试跑，再扩大到更多 trials 和 seeds。
4. 如果任务密度太低，三种策略都接近满分，差异不明显。
5. 如果任务密度太高，所有策略都可能失败率上升，这时更适合分析稳健性。
6. `--tag` 要写清楚实验目的，避免覆盖已有结果。

## 11. 一个完整示例

第一步，跑基线：

```powershell
python -m ev_dispatch.benchmark --tag baseline
```

第二步，自动调参：

```powershell
python -m ev_dispatch.tune_composite --scales small_city --seeds 42,43,44,45,46 --trials 60 --steps 24 --tasks-per-step 4 --stdev-penalty 0.2 --tag small_robust
```

第三步，把 `outputs/tuning/composite_tuning_small_robust.json` 中的 `best_config` 加入 `COMPOSITE_CONFIG_PRESETS`。

第四步，验证调参效果：

```powershell
python -m ev_dispatch.benchmark --strategies nearest,largest,composite,composite:tuned --seeds 42,43,44,45,46 --tag tuned_compare
```

第五步，打开：

```text
outputs/benchmarks/summary_tuned_compare.csv
```

比较 `composite` 和 `composite:tuned` 的完成率、准时率、单任务成本、单任务里程和总分。
