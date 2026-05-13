# ABC 对接文档（协同开发版）

## 1. 文档目标
本文件用于明确成员 A（算法）、成员 B（仿真）、成员 C（可视化）之间的接口边界、调用顺序和联调验收标准，确保三方可并行开发并快速集成。

## 2. 当前代码基线
1. 动作与状态接口定义在 [ev_dispatch/core/interfaces.py](ev_dispatch/core/interfaces.py)。
2. 仿真主循环定义在 [ev_dispatch/simulator/simulator.py](ev_dispatch/simulator/simulator.py)。
3. 策略实现入口在 [ev_dispatch/algorithms/dispatcher.py](ev_dispatch/algorithms/dispatcher.py) 与 [ev_dispatch/algorithms/strategies.py](ev_dispatch/algorithms/strategies.py)。

## 3. 角色边界与责任
1. 成员 A（算法）
- 只负责根据状态生成动作，不直接修改仿真内部状态。
- 通过实现 generate_actions(state) 输出调度决策。
- 可新增策略文件，但必须遵守 Action 输出格式。

2. 成员 B（仿真）
- 负责状态演进、物理约束、计分和动作执行。
- 负责把每个时间步转换为 SimulationState，调用 A 的策略。
- 负责生成 SimulationFrame 给 C 消费。

3. 成员 C（可视化）
- 不直接改动调度与物理逻辑。
- 只消费 SimulationFrame 和结果统计进行展示。
- 负责演示界面、图表和导出报告。

## 4. 对接接口定义（最终以代码为准）
1. Action（A -> B）
- type: 动作类型（当前已用 assign_task）
- vehicle_id: 车辆 ID
- task_id: 任务 ID，可选
- station_id: 充电站 ID，可选
- note: 调试备注

2. SimulationState（B -> A）
- current_time: 当前时间
- pending_tasks: 待处理任务列表
- vehicles: 车辆状态列表
- charging_stations: 充电站状态列表
- network: 路网对象
- extra_info: 扩展字段（如 step）

3. SimulationFrame（B -> C）
- current_time: 当前时间
- step: 时间步
- vehicle_positions: 车辆坐标字典
- vehicle_battery: 车辆电量字典
- pending_task_ids: 待处理任务 ID 列表
- completed_task_ids: 已完成任务 ID 列表
- failed_task_ids: 失败任务 ID 列表

## 5. 调用时序（联调主链路）
1. B 生成新任务并构造 SimulationState。
2. B 调用 A.generate_actions(state) 获取动作列表。
3. B 执行动作并更新车辆、任务、得分。
4. B 生成本步 SimulationFrame。
5. C 读取 get_frames() 或 get_latest_frame() 进行渲染。

## 6. 约束与约定
1. A 不得在策略函数里直接写入车辆真实状态，规划阶段若需临时容量计算，使用局部变量。
2. B 是唯一状态真源，只有 B 可以决定动作是否生效（例如电量不足导致动作失败）。
3. C 仅读取数据，不反向修改仿真对象。
4. 新增动作类型时，A 和 B 必须同步更新：
- A: 输出新动作
- B: 在 _execute_action 中实现该动作语义
- C: 可选增加对应动画表现

## 7. 最小联调示例
1. A 输出动作
```python
return [
    Action(type="assign_task", vehicle_id="vehicle_0", task_id="task_3", note="nearest")
]
```

2. B 执行与产帧
- B 在 run_simulation 中读取 actions。
- B 对每个动作调用 _execute_action。
- B 在每步结束 append 一帧到 self.frames。

3. C 消费数据
```python
frames = simulator.get_frames()
latest = simulator.get_latest_frame()
```

## 8. 验收清单（每次集成前必须通过）
1. 运行 python -m ev_dispatch.main 无异常退出。
2. 至少一个策略可返回非空动作列表。
3. 仿真结束后 get_frames() 长度等于 num_steps。
4. 每帧中 vehicle_positions 与 vehicle_battery 键集合一致。
5. 可视化端可读取最新帧并显示 step、待处理任务数。

## 9. 分支协作建议
1. A 使用 feature/algorithm-* 分支，重点改 algorithms 与 network。
2. B 使用 feature/simulator-* 分支，重点改 simulator、core、scenarios。
3. C 使用 feature/visual-* 分支，重点改 visualization、main。
4. 合并顺序建议：B 基线先合入，A 跟进策略，最后 C 接展示层。

## 10. 常见问题
1. 现象：策略输出了动作但任务没有完成。
- 检查 action.task_id 是否存在于当步 task_map。
- 检查 B 的 _execute_action 是否支持该 action.type。

2. 现象：可视化显示空数据。
- 检查 B 是否在每步 append 了 SimulationFrame。
- 检查 C 读取的是同一个 simulator 实例。

3. 现象：A 改完后结果波动极大。
- 固定随机种子再比较。
- 单独记录每步动作与分数增量做回放。

---
维护人：A/B/C 全体
最后更新：2026-04-21
