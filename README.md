# OpenClaw 飞书项目决策记忆插件

这是一个面向飞书项目协作场景的 OpenClaw 插件 MVP。插件的目标不是做普通聊天摘要，而是帮助项目群沉淀“已经决定过什么、为什么这样决定、证据在哪里、后续讨论是否需要更新旧决策”。

## 核心功能

- 读取飞书文档或飞书群聊上下文，并交给 OpenClaw 调用插件分析。
- 从非结构化讨论中提取项目决策、理由、反对意见、结论、阶段和时间点。
- 生成结构化项目决策卡片，便于在群聊中展示和复核。
- 为决策卡片保留证据链，包括来源、原文片段、时间和引用关系。
- 检索历史相似决策，辅助判断新内容是新建、更新、重复还是冲突。
- 提供 MARS Memory Engine 本地测试、benchmark 和 consolidation eval，便于回归验证。

## 适用场景

- 项目群里频繁出现关键决策，但后续容易遗忘。
- 新成员加入后需要快速理解项目背景和历史选择。
- 团队重复讨论“之前为什么不用某个方案”。
- 阶段总结、复赛安排、技术方案、风险限制等内容需要沉淀为可追溯记录。

## AI 亮点

- 将普通摘要升级为项目决策记忆。
- 使用结构化抽取，将讨论转为决策、理由、反对意见、结论和时间线。
- 引入证据链，降低模型幻觉风险。
- 引入生命周期判断，支持 new / update / duplicate / conflict。
- 通过 LLM Provider Boundary 支持后续切换 DeepSeek、GLM 或 OpenAI-compatible 模型。

## 当前状态

当前版本是可执行、可测试、可演示的 MVP：

- 已支持 OpenClaw 插件接入。
- 已支持飞书文档到决策卡片的演示链路。
- 已支持 MARS 本地记忆引擎、检索、证据链和相似决策判断。
- 已通过本地单元测试和 benchmark。

仍在后续完善的能力：

- 飞书交互卡片按钮闭环。
- 飞书多维表格治理写入和回写。
- 全量历史群聊读取和主动推送。
- 生产级权限过滤和审计日志。

## 最小运行思路

使用者需要准备：

- OpenClaw 运行环境。
- 飞书应用 app_id / app_secret 和相应权限。
- 模型 API，例如 DeepSeek、GLM 或其他 OpenAI-compatible API。
- Python 环境，用于运行 MARS Memory Engine。

典型流程：

```text
飞书群聊或文档
  -> OpenClaw 接收指令
  -> 调用飞书读取工具
  -> 调用本插件的 MARS 工具
  -> 生成项目决策卡片
  -> 返回给飞书用户确认
```

## 测试能力

项目包含本地回归测试设计：

```powershell
cd mars-memory-engine
py -m unittest discover tests
py -m mars_memory_engine run-benchmark --json
py -m mars_memory_engine consolidation-eval --json
```

benchmark 主要验证：

- 主题切分是否正常。
- 噪声下是否能召回决策记忆。
- 风险查询意图是否能识别。
- 重复、更新、支持关系是否能判断。

## 项目定位

本项目主线是 OpenClaw + 飞书项目协作场景下的决策记忆插件。底层记忆设计参考了 MARS 类记忆系统思想，但项目重点是将记忆能力产品化到飞书项目群里，解决项目决策沉淀、证据追溯、历史召回和后续治理问题。
