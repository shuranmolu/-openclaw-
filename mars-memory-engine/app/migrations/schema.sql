-- MARS P0 SQLite Schema
-- 版本: 1.0
-- 说明: 企业记忆引擎核心数据表，支持 Raw Ledger、Memory Objects、Reconciliation、Benchmark

-- ============================================================================
-- 1. Raw Ledger Layer - 原始事件账本
-- ============================================================================

-- 原始事件表 - append-only，所有记忆的溯源基础
CREATE TABLE IF NOT EXISTS raw_events (
    -- 主键
    event_id TEXT PRIMARY KEY,

    -- 事件分类
    event_type TEXT NOT NULL,              -- message.created, message.updated, document.created, etc.
    source_type TEXT NOT NULL,             -- feishu_chat, feishu_doc, cli, sample_json
    source_id TEXT NOT NULL,               -- 原始消息ID/文档ID，用于幂等检查

    -- 租户与项目
    tenant_id TEXT NOT NULL DEFAULT 'default_tenant',
    project_id TEXT,                       -- 所属项目，NULL 表示通用

    -- 上下文信息
    chat_id TEXT,                          -- 群聊ID
    actor_id TEXT,                         -- 发言人ID

    -- 内容
    content TEXT NOT NULL,                 -- 原始文本内容
    payload_json TEXT,                     -- 扩展信息 JSON（附件、@人等）

    -- 双时态支持
    transaction_time TEXT NOT NULL,        -- 系统记录时间
    valid_time_start TEXT NOT NULL,        -- 事件发生时间
    valid_time_end TEXT,                   -- NULL 表示永久有效

    -- 系统字段
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 原始事件索引：幂等检查 + 检索优化
CREATE UNIQUE INDEX IF NOT EXISTS idx_raw_events_source ON raw_events(source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_raw_events_project ON raw_events(project_id);
CREATE INDEX IF NOT EXISTS idx_raw_events_time ON raw_events(valid_time_start);
CREATE INDEX IF NOT EXISTS idx_raw_events_actor ON raw_events(actor_id);

-- ============================================================================
-- 2. Memory Objects - 结构化记忆对象
-- ============================================================================

-- 记忆对象表 - 核心记忆存储
CREATE TABLE IF NOT EXISTS memory_objects (
    -- 主键
    memory_id TEXT PRIMARY KEY,

    -- 记忆分类
    memory_type TEXT NOT NULL,             -- decision, fact, procedure, risk, preference
    scope TEXT NOT NULL,                   -- user, team, project, org

    -- 租户与关联
    tenant_id TEXT NOT NULL DEFAULT 'default_tenant',
    project_id TEXT,                       -- 所属项目
    user_id TEXT,                          -- 所属用户（scope=user 时）

    -- 记忆内容
    topic TEXT NOT NULL,                   -- 主题分类（技术路线、接口、周报等）
    title TEXT NOT NULL,                   -- 简短标题
    content TEXT NOT NULL,                 -- 详细内容

    -- 理由与反对意见（JSON 数组字符串）
    rationale_json TEXT,                   -- ["理由1", "理由2"]
    objections_json TEXT,                  -- ["反对意见1"]

    -- 标签（JSON 数组字符串）
    tags_json TEXT,                        -- ["前端", "API"]

    -- 状态与版本
    status TEXT NOT NULL DEFAULT 'pending',-- pending, active, superseded, expired, conflicted, archived, rejected
    version INTEGER NOT NULL DEFAULT 1,

    -- 置信度与重要性
    confidence REAL NOT NULL DEFAULT 0.5,  -- 0.0 ~ 1.0
    importance INTEGER NOT NULL DEFAULT 3, -- 1 ~ 5

    -- 时间信息
    valid_time_start TEXT NOT NULL,        -- 记忆生效时间
    valid_time_end TEXT,                   -- NULL 表示永久有效
    transaction_time TEXT NOT NULL,        -- 记忆写入时间

    -- 版本链
    supersedes TEXT,                       -- 覆盖的记忆 ID
    superseded_by TEXT,                    -- 被哪个记忆覆盖

    -- 系统字段
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 记忆对象索引
CREATE INDEX IF NOT EXISTS idx_memory_objects_project ON memory_objects(project_id);
CREATE INDEX IF NOT EXISTS idx_memory_objects_type ON memory_objects(memory_type);
CREATE INDEX IF NOT EXISTS idx_memory_objects_status ON memory_objects(status);
CREATE INDEX IF NOT EXISTS idx_memory_objects_topic ON memory_objects(topic);
CREATE INDEX IF NOT EXISTS idx_memory_objects_supersedes ON memory_objects(supersedes);
CREATE INDEX IF NOT EXISTS idx_memory_objects_superseded_by ON memory_objects(superseded_by);

-- 状态约束
CREATE TRIGGER IF NOT EXISTS validate_memory_status
BEFORE INSERT ON memory_objects
WHEN NEW.status NOT IN ('pending', 'active', 'superseded', 'expired', 'conflicted', 'archived', 'rejected')
BEGIN
    SELECT RAISE(ABORT, 'Invalid memory status');
END;

-- 置信度约束
CREATE TRIGGER IF NOT EXISTS validate_memory_confidence
BEFORE INSERT ON memory_objects
WHEN NEW.confidence < 0.0 OR NEW.confidence > 1.0
BEGIN
    SELECT RAISE(ABORT, 'Confidence must be between 0.0 and 1.0');
END;

-- 重要性约束
CREATE TRIGGER IF NOT EXISTS validate_memory_importance
BEFORE INSERT ON memory_objects
WHEN NEW.importance < 1 OR NEW.importance > 5
BEGIN
    SELECT RAISE(ABORT, 'Importance must be between 1 and 5');
END;

-- ============================================================================
-- 3. Memory Sources - 记忆证据溯源
-- ============================================================================

-- 记忆来源表 - 每条记忆可追溯到的原始事件
CREATE TABLE IF NOT EXISTS memory_sources (
    id TEXT PRIMARY KEY,                   -- src_xxx
    memory_id TEXT NOT NULL,               -- 关联的记忆 ID
    event_id TEXT NOT NULL,                -- 关联的原始事件 ID

    -- 证据信息
    evidence_type TEXT NOT NULL,           -- quote, reference, derivation
    quote TEXT,                            -- 直接引用原文片段
    source_url TEXT,                       -- 飞书消息链接/文档链接

    -- 系统字段
    created_at TEXT NOT NULL DEFAULT (datetime('now')),

    FOREIGN KEY (memory_id) REFERENCES memory_objects(memory_id) ON DELETE CASCADE,
    FOREIGN KEY (event_id) REFERENCES raw_events(event_id) ON DELETE CASCADE
);

-- 记忆来源索引
CREATE INDEX IF NOT EXISTS idx_memory_sources_memory ON memory_sources(memory_id);
CREATE INDEX IF NOT EXISTS idx_memory_sources_event ON memory_sources(event_id);

-- ============================================================================
-- 4. Memory Edges - 记忆关系图
-- ============================================================================

-- 记忆边表 - 记忆之间的关系（重复、支持、冲突、覆盖等）
CREATE TABLE IF NOT EXISTS memory_edges (
    edge_id TEXT PRIMARY KEY,              -- edge_xxx
    source_memory_id TEXT NOT NULL,        -- 起点
    target_memory_id TEXT NOT NULL,        -- 终点

    -- 关系类型
    relation_type TEXT NOT NULL,           -- duplicate, support, update, conflict, supersede, unrelated

    -- 关系信息
    reason TEXT,                           -- 关系判断理由
    confidence REAL,                       -- 关系置信度

    -- 系统字段
    created_at TEXT NOT NULL DEFAULT (datetime('now')),

    FOREIGN KEY (source_memory_id) REFERENCES memory_objects(memory_id) ON DELETE CASCADE,
    FOREIGN KEY (target_memory_id) REFERENCES memory_objects(memory_id) ON DELETE CASCADE
);

-- 记忆边索引
CREATE INDEX IF NOT EXISTS idx_memory_edges_source ON memory_edges(source_memory_id);
CREATE INDEX IF NOT EXISTS idx_memory_edges_target ON memory_edges(target_memory_id);
CREATE INDEX IF NOT EXISTS idx_memory_edges_type ON memory_edges(relation_type);

-- 防止自环
CREATE TRIGGER IF NOT EXISTS prevent_self_loop
BEFORE INSERT ON memory_edges
WHEN NEW.source_memory_id = NEW.target_memory_id
BEGIN
    SELECT RAISE(ABORT, 'Memory edge cannot be a self-loop');
END;

-- ============================================================================
-- 5. Memory Candidates - 候选记忆暂存
-- ============================================================================

-- 候选记忆表 - LLM 抽取后、人工确认前
CREATE TABLE IF NOT EXISTS memory_candidates (
    candidate_id TEXT PRIMARY KEY,         -- cand_xxx

    -- 候选信息
    candidate_type TEXT NOT NULL,          -- decision, fact, procedure, risk
    topic TEXT NOT NULL,
    summary TEXT NOT NULL,

    -- 关联证据
    evidence_event_ids TEXT NOT NULL,      -- JSON 数组 ["evt_001", "evt_002"]

    -- 项目上下文
    project_id TEXT NOT NULL,

    -- 抽取信息
    confidence REAL NOT NULL DEFAULT 0.5,
    need_human_confirm INTEGER NOT NULL DEFAULT 0, -- 布尔值

    -- 系统字段
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 候选记忆索引
CREATE INDEX IF NOT EXISTS idx_memory_candidates_project ON memory_candidates(project_id);
CREATE INDEX IF NOT EXISTS idx_memory_candidates_type ON memory_candidates(candidate_type);
CREATE INDEX IF NOT EXISTS idx_memory_candidates_confirm ON memory_candidates(need_human_confirm);

-- ============================================================================
-- 6. Windows - 讨论窗口（P0 可选）
-- ============================================================================

-- 讨论窗口表 - 用于记忆提取的输入分组
CREATE TABLE IF NOT EXISTS windows (
    window_id TEXT PRIMARY KEY,            -- win_xxx

    -- 窗口信息
    project_id TEXT NOT NULL,
    topic_hint TEXT,                       -- 主题提示

    -- 事件关联
    event_ids TEXT NOT NULL,               -- JSON 数组 ["evt_001", "evt_002"]

    -- 时间范围
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,

    -- 系统字段
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 讨论窗口索引
CREATE INDEX IF NOT EXISTS idx_windows_project ON windows(project_id);
CREATE INDEX IF NOT EXISTS idx_windows_time ON windows(start_time);

-- ============================================================================
-- 7. Retrieval Logs - Retrieval audit log
-- ============================================================================

CREATE TABLE IF NOT EXISTS retrieval_logs (
    log_id TEXT PRIMARY KEY,
    query TEXT NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT 'default_tenant',
    project_id TEXT,
    chat_id TEXT,
    requester_id TEXT,
    time_scope TEXT,
    top_k INTEGER,
    status_filter TEXT,
    retrieval_method TEXT,
    retrieved_memory_ids_json TEXT,
    selected_memory_ids_json TEXT,
    score_json TEXT,
    latency_ms INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_retrieval_logs_project_time ON retrieval_logs(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_retrieval_logs_query ON retrieval_logs(query);

-- ============================================================================
-- 初始化与维护
-- ============================================================================

-- 创建元数据表（记录 schema 版本）
CREATE TABLE IF NOT EXISTS schema_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- 记录当前 schema 版本
INSERT OR REPLACE INTO schema_metadata (key, value) VALUES ('schema_version', '1.0');
INSERT OR REPLACE INTO schema_metadata (key, value) VALUES ('schema_updated_at', datetime('now'));
