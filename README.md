# OpenClaw Feishu Decision Memory Plugin

这是一个面向飞书项目协作场景的 OpenClaw 插件工程。它把群聊、文档和阶段总结中的非结构化讨论，沉淀为可检索、可追溯、可更新的项目决策记忆。

## 核心能力

- 读取飞书群聊或飞书文档内容，并交给 OpenClaw 工具链分析。
- 从讨论中提取决策、理由、反对意见、结论、阶段、时间点和证据片段。
- 生成结构化项目决策卡片，用于群聊展示、复盘和检索。
- 为每条决策保留证据链，降低摘要幻觉和误归因风险。
- 检索历史相似决策，辅助判断新内容是新建、更新、重复还是冲突。
- 支持主动唤醒式调用，由用户在飞书中通过 OpenClaw 指令触发插件流程。

## 功能实现原理

- 飞书内容读取：通过 OpenClaw 的飞书扩展读取群聊消息、单条消息或文档内容，再把原文、来源和上下文作为插件输入。
- 决策信息抽取：插件把原始文本交给 MARS 记忆引擎处理，先做文本切分和候选记忆抽取，再识别其中的决策、理由、结论、风险、反对意见和时间信息。
- 决策卡片生成：抽取结果会被整理成固定字段，包括决策、理由、结论、阶段、时间、风险、待办和相关主题，最后格式化为飞书中可读的结构化卡片。
- 证据链保留：每条决策都会关联原文片段、来源类型、来源 ID、时间和 revision，方便后续追溯“这个结论从哪里来”。
- 历史决策检索：新内容写入前会先检索已有记忆，根据主题、关键词、时间和语义相似度找出可能相关的旧决策。
- 合并与冲突判断：插件会把相似候选、证据和启发式判断返回给 OpenClaw，由 OpenClaw 决定是新建、更新、去重还是标记冲突。
- 主动唤醒：当前版本以用户主动指令触发为主，例如在飞书里要求 OpenClaw 读取某段群聊或文档，并调用决策记忆插件生成结果。

## 目录说明

```text
agent/
  extensions/
    openclaw-feishu-memory-plugin/   # 飞书项目决策记忆插件
    openclaw-lark/                   # 飞书/OpenClaw 扩展能力
    openclaw-guardian-plugin/        # OpenClaw 运行辅助扩展
    openclaw-extension-miaoda*/      # 本地 OpenClaw 扩展
  skills/                            # OpenClaw 技能定义
  package.json                       # agent 依赖入口
mars-memory-engine/                  # 本地 MARS 记忆引擎与 benchmark
```

## 本地运行

1. 安装 OpenClaw 运行环境和 Node/Python 依赖。
2. 在本地生成或恢复自己的 `agent/openclaw.json`。
3. 配置飞书应用的 `app_id`、`app_secret`、事件订阅和消息/文档读取权限。
4. 配置模型服务，例如 DeepSeek、GLM 或其他 OpenAI-compatible API。
5. 启动 OpenClaw 后，在飞书中通过主动指令调用插件。

典型链路：

```text
飞书群聊/文档
  -> OpenClaw 接收用户指令
  -> 飞书读取工具获取上下文
  -> MARS 决策记忆插件抽取和检索
  -> OpenClaw 判断合并、更新、冲突或新建
  -> 返回结构化决策卡片
```

## 测试

本地 MARS 引擎支持单元测试和 benchmark：

```powershell
cd mars-memory-engine
py -m unittest discover tests
py -m mars_memory_engine run-benchmark --json
py -m mars_memory_engine consolidation-eval --json
```

测试重点包括：

- 是否能从长文本中抽取决策、理由、结论和时间线。
- 是否能保留证据链。
- 是否能检索历史相似决策。
- 是否能区分新建、更新、重复和冲突。
- 是否能在 OpenClaw 插件链路中稳定返回飞书可读结果。
