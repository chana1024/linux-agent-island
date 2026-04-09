• 生命周期模型

  这个项目把 session 生命周期拆成 5 个环节：

  1. 恢复
  2. 创建
  3. 更新
  4. 存活判定
  5. 移除

  真正的核心不是某个 agent 的 hook，而是 SessionState 这个 reducer。所有外部信号最后都会收敛成 AgentEvent，再由 Sources/OpenIslandCore/SessionState.swift:56 更新同一个 AgentSession 模型。

  1. 恢复

  应用启动时不会等新 hook 才显示 session，而是先恢复最近状态：

  - 从本地持久化恢复最近 session
  - 从 Codex rollout / Claude transcript 扫描恢复 session
  - 然后立刻开始 live bridge 和进程轮询

  入口在 Sources/OpenIslandApp/AppModel.swift:430 和 Sources/OpenIslandApp/SessionDiscoveryCoordinator.swift:67。

  恢复出来的 session 本质上是“候选活跃 session”，后面还要经过进程和 hook 信号确认。

  2. 创建

  创建 session 有两条路：

  - hook 事件直接创建
  - 启动扫描从本地文件恢复创建

  实时创建时，bridge 把 hook 翻译成 sessionStarted，例如 Codex 走 Sources/OpenIslandCore/BridgeServer.swift:388。

  落到 reducer 时会初始化一个 AgentSession，包括：

  - id / tool / title
  - phase
  - jumpTarget
  - 私有 metadata
  - isProcessAlive = true
  - processNotSeenCount = 0

  见 Sources/OpenIslandCore/SessionState.swift:58。

  3. 更新

  session 一旦存在，后续不再新建，而是持续更新同一个对象：

  - prompt -> activityUpdated
  - tool/metadata -> sessionMetadataUpdated
  - permission -> permissionRequested
  - question -> questionAsked
  - stop/turn complete -> sessionCompleted

  对应状态变更也都在 Sources/OpenIslandCore/SessionState.swift:80。

  这里一个关键点是：

  completed 不等于 session 已死亡。

  它通常只表示“一轮任务结束了”，不是进程退出。真正的销毁还要看后面的存活判定。

  4. 存活判定

  这部分是这个项目生命周期模型最关键的地方。

  session 是否继续存在，不由单一 hook 决定，而由两类信号共同决定：

  - hook 生命周期信号
  - 进程探测信号

  ProcessMonitoringCoordinator 每 2 秒做一次 reconcile，见 Sources/OpenIslandApp/ProcessMonitoringCoordinator.swift:47 和 Sources/OpenIslandApp/ProcessMonitoringCoordinator.swift:78。

  它会：

  - 发现本地 agent 进程
  - 计算哪些 session 还活着
  - 更新 jump target
  - 删除已经不可见的 session

  真正的 liveness 规则在 Sources/OpenIslandCore/SessionState.swift:297：

  - 如果进程还在，isProcessAlive = true，processNotSeenCount = 0
  - 如果进程没找到，processNotSeenCount += 1
  - 连续 2 次没找到，认为 session 已经 gone

  也就是说，它用一个很轻的“2 次轮询缺失”机制避免短暂 ps/lsof 抖动。

  5. 可见与移除

  最后 session 是否还留在 UI/state，不是看 phase，而是看 isVisibleInIsland，定义在 Sources/OpenIslandCore/AgentSession.swift:433：

  - demo session 永远可见
  - 需要用户处理的 session 可见
  - hook-managed session 在没收到结束信号前可见
  - 非 hook-managed session 在进程活着时可见

  当 session 不再可见时，会被真正从状态里移除，见 Sources/OpenIslandCore/SessionState.swift:360。

  这个模型的本质

  可以把它压缩成一句话：

  hook 负责“发生了什么”，process discovery 负责“还活着吗”，SessionState 负责“现在这个 session 处于什么生命周期阶段”。

  所以它不是“事件驱动删除模型”，而是“事件驱动更新 + 轮询驱动回收”的混合生命周期模型。

  如果你愿意，我可以下一条继续专门拆一下这个模型里最容易混淆的两件事：

  - completed 和 ended 的区别
  - hook-managed 和 process-managed 的区别

