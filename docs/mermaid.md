flowchart TD
    A["入口: Simulator.run_simulation(num_steps, tasks_per_step)"] --> A0["初始化累计变量:
    total_distance/total_time_hours/total_score/total_cost/total_generated = 0
    _dbg('simulation_start', ...)"]

    A0 --> B{"for step in range(num_steps)"}

    B --> C["调用 _advance_charging_stations()
    1) station.complete_charging(...)
    2) vehicle.end_charging()
    3) station.dequeue()/start_charging(...)"]

    C --> D["生成新任务:
    new_tasks = [generate_random_task(current_time) ...]
    total_generated += len(new_tasks)"]

    D --> E["全局任务池滚动:
    self.all_tasks.extend(new_tasks)
    task_map = {t.id: t for t in self.all_tasks}"]

    E --> F["构建状态快照:
    state = SimulationState(..., tasks=self.all_tasks, ...)
    关键过滤: pending_tasks = [t for t in tasks if not t.completed and not t.failed]"]

    F --> G["调度决策:
    actions = dispatcher.generate_actions(state)
    (如 DispatcherNearestFirst / DispatcherLargestFirst)"]

    G --> H["_dbg('step_actions', generated_tasks/pending_task_ids/actions)"]

    H --> I{"遍历 actions"}

    I --> J["执行动作: _execute_action(action, task_map)"]
    J --> J1{"action.type"}
    J1 -->|go_charge| J2["找站点 _find_station()
    shortest_distance + shortest_travel_time_hours
    EnergyManager.calculate_consumption
    station.enqueue(vehicle.id)
    _advance_charging_stations()"]
    J1 -->|assign_task| J3["task = task_map.get(action.task_id)
    计算取货/配送距离与时间
    计算能耗与运输成本
    货类校验/容量校验
    更新 task.completed 或 task.failed
    更新 completed_tasks/failed_tasks 与 score"]
    J2 --> K["返回 delta: distance/score/time_hours/cost"]
    J3 --> K

    K --> L["累计:
    total_distance += delta['distance']
    total_time_hours += delta['time_hours']
    total_score += delta['score']
    total_cost += delta.get('cost', 0.0)"]

    L --> M["步进时间:
    current_time += timedelta(hours=1)"]

    M --> N["记录待处理任务:
    pending = [t for t in self.all_tasks if not t.completed and not t.failed]
    frames.append(_build_frame(step, pending))"]

    N --> B

    B -->|循环结束| O["返回结果字典:
    completed/failed/generated/total_distance/
    total_time_hours/total_cost/total_score/
    avg_score_per_task"]
