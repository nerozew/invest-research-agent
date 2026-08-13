# 数据库设计

## 1. 设计原则

- PostgreSQL 是任务状态、来源、事实、指标和报告元数据的唯一真相源。
- 大文件和完整原文放对象存储；数据库只保存结构化内容、摘要、URI 和 checksum。
- 原始事实不可更新覆盖。口径修复通过新增解析版本或事实版本实现。
- 金额和比例使用 `numeric`，Python 侧使用 `Decimal`。
- JSONB 只承载变化快或供应商特有的元数据；核心查询字段必须是普通列。
- 每个外部调用和工作流步骤可审计，但敏感信息和大段原文不入日志表。

## 2. ER 关系

```mermaid
erDiagram
    COMPANIES ||--o{ RESEARCH_JOBS : has
    COMPANIES ||--o{ FILINGS : submits
    RESEARCH_JOBS ||--o{ WORKFLOW_STEPS : contains
    RESEARCH_JOBS ||--o{ JOB_SOURCES : uses
    SOURCES ||--o{ JOB_SOURCES : linked
    SOURCES ||--o{ DOCUMENTS : materializes
    FILINGS ||--o{ DOCUMENTS : has
    DOCUMENTS ||--o{ DOCUMENT_CHUNKS : contains
    FILINGS ||--o{ FINANCIAL_FACTS : provides
    RESEARCH_JOBS ||--o{ COMPUTED_METRICS : derives
    RESEARCH_JOBS ||--|| REPORTS : produces
    REPORTS ||--o{ CITATIONS : contains
    SOURCES ||--o{ CITATIONS : supports
    RESEARCH_JOBS ||--o{ TOOL_INVOCATIONS : records
    WORKFLOW_STEPS ||--o{ TOOL_INVOCATIONS : invokes
    RESEARCH_JOBS ||--o{ ARTIFACTS : emits
```

## 3. 表职责

| 表 | 作用 | 关键约束 |
|---|---|---|
| `companies` | 规范化公司身份 | CIK 唯一且固定为 10 位 |
| `research_jobs` | 用户请求和整体状态 | 保存配置快照与数据截止日 |
| `workflow_steps` | 每一步的状态和结构化 I/O | `(job_id, step_name)` 唯一，支持幂等 |
| `sources` | 外部来源的规范化目录 | `canonical_url` 唯一、带访问时间和 checksum |
| `job_sources` | 任务与来源的多对多关系 | 保存用途、相关度和入选原因 |
| `filings` | SEC 申报元数据 | accession number 唯一 |
| `documents` | 下载文件和解析状态 | 原始内容通过 `storage_uri` 指向对象存储 |
| `document_chunks` | 可检索的规范化文本块 | 文档内 chunk index 唯一 |
| `financial_facts` | XBRL/申报事实 | 保存 concept、期间、单位和来源 |
| `computed_metrics` | 版本化派生指标 | 保存公式版本和所有输入引用 |
| `reports` | 最终报告版本 | 一个 job 可通过 version 保留历史版本 |
| `citations` | 报告 claim 与 source 的映射 | 保存 locator 和门禁结果 |
| `tool_invocations` | 工具审计与性能 | 请求字段脱敏，支持幂等键 |
| `artifacts` | 中间工件 manifest | `(job_id, artifact_key)` 唯一 |

## 4. PostgreSQL DDL 草案

这是一份设计基线，不替代 Alembic migration。实现时每个 migration 只做一个可回滚的 schema 变化。

```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE companies (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    cik char(10) NOT NULL UNIQUE CHECK (cik ~ '^[0-9]{10}$'),
    ticker text,
    legal_name text NOT NULL,
    exchange text,
    sic text,
    fiscal_year_end char(4),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_companies_ticker_upper
    ON companies (upper(ticker)) WHERE ticker IS NOT NULL;

CREATE TABLE research_jobs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id uuid REFERENCES companies(id),
    input_company text NOT NULL,
    input_ticker text,
    as_of_date date NOT NULL,
    language text NOT NULL DEFAULT 'zh-CN',
    requested_forms jsonb NOT NULL DEFAULT '["10-K", "10-Q"]'::jsonb,
    status text NOT NULL CHECK (
        status IN ('pending', 'running', 'succeeded', 'partial', 'failed', 'cancelled')
    ),
    current_step text,
    config_snapshot jsonb NOT NULL,
    idempotency_key text UNIQUE,
    error_code text,
    error_message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    completed_at timestamptz,
    CHECK (completed_at IS NULL OR started_at IS NOT NULL)
);
CREATE INDEX ix_research_jobs_status_created
    ON research_jobs (status, created_at DESC);
CREATE INDEX ix_research_jobs_company_created
    ON research_jobs (company_id, created_at DESC);

CREATE TABLE workflow_steps (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id uuid NOT NULL REFERENCES research_jobs(id) ON DELETE CASCADE,
    step_name text NOT NULL,
    sequence_no smallint NOT NULL,
    status text NOT NULL CHECK (
        status IN ('pending', 'running', 'succeeded', 'failed_retryable',
                   'failed_terminal', 'skipped')
    ),
    attempt_count smallint NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    input_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    output_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    error_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    output_schema_version text,
    started_at timestamptz,
    completed_at timestamptz,
    UNIQUE (job_id, step_name),
    UNIQUE (job_id, sequence_no)
);
CREATE INDEX ix_workflow_steps_job_sequence
    ON workflow_steps (job_id, sequence_no);

CREATE TABLE sources (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_type text NOT NULL CHECK (
        source_type IN ('sec_filing', 'sec_xbrl', 'web', 'company_ir', 'uploaded')
    ),
    canonical_url text NOT NULL UNIQUE,
    title text,
    publisher text,
    published_at timestamptz,
    accessed_at timestamptz NOT NULL,
    content_checksum text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_sources_publisher_published
    ON sources (publisher, published_at DESC);

CREATE TABLE job_sources (
    job_id uuid NOT NULL REFERENCES research_jobs(id) ON DELETE CASCADE,
    source_id uuid NOT NULL REFERENCES sources(id) ON DELETE RESTRICT,
    purpose text NOT NULL,
    relevance_score numeric(5,4),
    selection_reason text,
    PRIMARY KEY (job_id, source_id, purpose),
    CHECK (relevance_score IS NULL OR relevance_score BETWEEN 0 AND 1)
);

CREATE TABLE filings (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id uuid NOT NULL REFERENCES companies(id),
    accession_number text NOT NULL UNIQUE,
    form_type text NOT NULL,
    filing_date date NOT NULL,
    report_period date,
    primary_document_url text NOT NULL,
    source_id uuid REFERENCES sources(id),
    is_amendment boolean NOT NULL DEFAULT false,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_filings_company_form_period
    ON filings (company_id, form_type, report_period DESC);

CREATE TABLE documents (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id uuid NOT NULL REFERENCES sources(id),
    filing_id uuid REFERENCES filings(id),
    media_type text NOT NULL,
    storage_uri text NOT NULL,
    content_checksum text NOT NULL,
    byte_size bigint NOT NULL CHECK (byte_size >= 0),
    parse_status text NOT NULL CHECK (
        parse_status IN ('pending', 'parsed', 'failed', 'unsupported')
    ),
    parser_name text,
    parser_version text,
    parsed_at timestamptz,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (content_checksum, parser_name, parser_version)
);

CREATE TABLE document_chunks (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index integer NOT NULL CHECK (chunk_index >= 0),
    section_path text,
    page_number integer,
    text_content text NOT NULL,
    token_count integer CHECK (token_count >= 0),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (document_id, chunk_index)
);
CREATE INDEX ix_document_chunks_fts
    ON document_chunks USING gin (to_tsvector('english', text_content));

CREATE TABLE financial_facts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id uuid NOT NULL REFERENCES companies(id),
    filing_id uuid REFERENCES filings(id),
    source_id uuid NOT NULL REFERENCES sources(id),
    taxonomy text NOT NULL,
    concept text NOT NULL,
    label text,
    value numeric(38,10) NOT NULL,
    unit text NOT NULL,
    period_start date,
    period_end date NOT NULL,
    instant_date date,
    fiscal_year integer,
    fiscal_period text,
    form_type text,
    frame text,
    accession_number text,
    fact_version text NOT NULL DEFAULT 'v1',
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    CHECK (
        (period_start IS NOT NULL AND instant_date IS NULL)
        OR (period_start IS NULL AND instant_date IS NOT NULL)
    )
);
CREATE INDEX ix_financial_facts_lookup
    ON financial_facts (company_id, concept, period_end DESC, form_type);
CREATE UNIQUE INDEX uq_financial_facts_identity
    ON financial_facts (
        company_id, source_id, taxonomy, concept, unit,
        COALESCE(period_start, DATE '0001-01-01'), period_end,
        COALESCE(accession_number, ''), fact_version
    );

CREATE TABLE computed_metrics (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id uuid NOT NULL REFERENCES research_jobs(id) ON DELETE CASCADE,
    metric_name text NOT NULL,
    period_end date NOT NULL,
    value numeric(38,10),
    unit text NOT NULL,
    status text NOT NULL CHECK (
        status IN ('computed', 'not_computable', 'ambiguous', 'failed_validation')
    ),
    formula_version text NOT NULL,
    inputs_json jsonb NOT NULL,
    explanation text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (job_id, metric_name, period_end, formula_version)
);

CREATE TABLE reports (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id uuid NOT NULL REFERENCES research_jobs(id) ON DELETE CASCADE,
    version integer NOT NULL CHECK (version > 0),
    status text NOT NULL CHECK (status IN ('draft', 'validated', 'published', 'rejected')),
    markdown_uri text,
    pdf_uri text,
    content_checksum text,
    prompt_version text NOT NULL,
    model_name text NOT NULL,
    quality_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    generated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (job_id, version)
);

CREATE TABLE citations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    report_id uuid NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    claim_key text NOT NULL,
    section_key text NOT NULL,
    source_id uuid NOT NULL REFERENCES sources(id),
    locator text,
    supports_claim boolean,
    validation_message text,
    UNIQUE (report_id, claim_key, source_id, locator)
);
CREATE INDEX ix_citations_report_section
    ON citations (report_id, section_key);

CREATE TABLE tool_invocations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id uuid NOT NULL REFERENCES research_jobs(id) ON DELETE CASCADE,
    workflow_step_id uuid REFERENCES workflow_steps(id) ON DELETE CASCADE,
    tool_name text NOT NULL,
    invocation_key text NOT NULL,
    status text NOT NULL CHECK (status IN ('running', 'succeeded', 'failed', 'cached')),
    attempt_no smallint NOT NULL CHECK (attempt_no > 0),
    request_redacted jsonb NOT NULL DEFAULT '{}'::jsonb,
    response_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    latency_ms integer CHECK (latency_ms >= 0),
    error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    UNIQUE (job_id, tool_name, invocation_key, attempt_no)
);

CREATE TABLE artifacts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id uuid NOT NULL REFERENCES research_jobs(id) ON DELETE CASCADE,
    workflow_step_id uuid REFERENCES workflow_steps(id),
    artifact_key text NOT NULL,
    artifact_type text NOT NULL,
    schema_version text,
    storage_uri text NOT NULL,
    content_checksum text NOT NULL,
    byte_size bigint NOT NULL CHECK (byte_size >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (job_id, artifact_key)
);
```

## 4.1 受控反思闭环持久化（Phase 3.5 评估结论）

结论：**现有表结构足够，不需要新增数据库表**。受控反思（修订/补证）通过现有的
`reports` 版本列、`artifacts` 工件与 `workflow_steps` 结构化 I/O 记录，全部可追溯。

| 需要记录的信息 | 现有落点 |
|---|---|
| 修订次数（≤1） | `workflow_steps.attempt_count`（第 06 步）或 `reports.version`（每次草稿递增） |
| 每次报告草稿 | `reports` 表 `version` 递增（`UNIQUE(job_id, version)` 已支持多版本）+ `prompt_version`/`model_name`/`quality_summary` |
| 每次质量报告 | `reports.quality_summary`（jsonb，存 QualityReport/QualityIssue 结构化） |
| 修订原因 / 补证原因 | `artifacts` + `workflow_steps.input_json/output_json`（结构化 RevisionRequest / SupplementResearchRequest） |
| 补证次数（≤1） | 第 02 步 `workflow_steps.attempt_count` 或 state 中 `supplement_attempt`（内存计数 + artifacts 留痕） |
| 每次生成的 artifact | `artifacts`（`job_id+artifact_key` 唯一，`workflow_step_id` 关联步骤） |
| 原始 vs 修订草稿的父子关系 | `artifacts` 的 `artifact_key` 命名约定（如 `05_report_draft_rev1.json`）或 metadata；不新增 `parent_artifact_id` 列 |

无需 migrations。若未来需要跨 job 查询"某 claim 被修订多少次"，再考虑新增表；当前 MVP 不需要。

## 5. 向量检索扩展（P2）

先完成基于章节、关键词和 PostgreSQL 全文检索的 MVP，再增加 pgvector。建议固定 BGE-M3 的 1024 维 embedding，并记录模型版本。

```sql
CREATE EXTENSION IF NOT EXISTS vector;
ALTER TABLE document_chunks ADD COLUMN embedding vector(1024);
ALTER TABLE document_chunks ADD COLUMN embedding_model text;

CREATE INDEX ix_document_chunks_embedding_hnsw
ON document_chunks USING hnsw (embedding vector_cosine_ops)
WHERE embedding IS NOT NULL;
```

更换 embedding 模型维度时新增列或新表迁移，不要把不同维度或不同模型的向量混入同一索引。

## 6. 事务与并发

- 创建 job 与预生成步骤记录放在同一事务。
- Worker 领取任务时使用队列保证一次投递倾向，同时数据库状态更新使用条件更新防止重复执行。
- 步骤状态从 `running` 到终态和工件元数据写入放在同一事务；文件先写临时名，checksum 成功后原子改名。
- 对工具结果使用 `invocation_key` 去重。网络重试产生新 attempt，但不得重复写同一业务事实。
- Redis 分布式锁只是优化；数据库唯一约束才是最终一致性防线。

## 7. 保留、脱敏与备份

- 开发环境工件默认保留 30 天，基准集结果长期保留。
- `tool_invocations.request_redacted` 禁止保存 API key、Authorization、Cookie 和完整网页正文。
- 原始 SEC 文件可复用；第三方网页只保存合规所需的摘要、元数据和内容 checksum。
- 数据库按开发、测试环境分开；本地使用 Docker named volume 持久化，并通过版本化 `pg_dump`、工件 checksum 和恢复演练验证备份可用性。
