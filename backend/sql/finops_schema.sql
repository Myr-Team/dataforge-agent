IF SCHEMA_ID(N'df_finops') IS NULL
BEGIN
    EXEC(N'CREATE SCHEMA df_finops AUTHORIZATION dbo');
END;

IF OBJECT_ID(N'df_finops.request_event', N'U') IS NULL
BEGIN
    CREATE TABLE df_finops.request_event (
        tenant_ref NVARCHAR(128) NOT NULL,
        request_ref NVARCHAR(128) NOT NULL,
        occurred_at DATETIME2(7) NOT NULL,
        call_class NVARCHAR(16) NOT NULL,
        department_id NVARCHAR(128) NULL,
        workspace_id NVARCHAR(160) NOT NULL,
        actor_ref NVARCHAR(128) NULL,
        run_id NVARCHAR(160) NULL,
        agent_id NVARCHAR(128) NULL,
        model_deployment NVARCHAR(160) NULL,
        route NVARCHAR(128) NULL,
        execution_kind NVARCHAR(64) NULL,
        request_status NVARCHAR(16) NOT NULL,
        error_category NVARCHAR(64) NULL,
        latency_ms INT NULL,
        total_tokens BIGINT NULL,
        cost_amount DECIMAL(20, 10) NULL,
        price_card_revision NVARCHAR(128) NULL,
        gateway_coverage NVARCHAR(24) NOT NULL,
        evidence_state NVARCHAR(16) NOT NULL,
        correlation_ref NVARCHAR(128) NULL,
        event_payload NVARCHAR(MAX) NOT NULL,
        ingested_at DATETIME2(7) NOT NULL
            CONSTRAINT DF_finops_request_ingested DEFAULT SYSUTCDATETIME(),
        updated_at DATETIME2(7) NOT NULL
            CONSTRAINT DF_finops_request_updated DEFAULT SYSUTCDATETIME(),
        CONSTRAINT PK_finops_request_event PRIMARY KEY (tenant_ref, request_ref),
        CONSTRAINT CK_finops_call_class CHECK (call_class IN (N'model', N'tool', N'embedding', N'image', N'speech', N'mcp')),
        CONSTRAINT CK_finops_request_status CHECK (request_status IN (N'succeeded', N'failed', N'cancelled', N'unknown')),
        CONSTRAINT CK_finops_gateway CHECK (gateway_coverage IN (N'apim_governed', N'app_observed', N'unmanaged', N'unknown')),
        CONSTRAINT CK_finops_evidence CHECK (evidence_state IN (N'observed', N'estimated', N'partial', N'unavailable')),
        CONSTRAINT CK_finops_event_json CHECK (ISJSON(event_payload) = 1)
    );
END;

IF OBJECT_ID(N'df_finops.request_rollup_hour', N'U') IS NULL
BEGIN
    CREATE TABLE df_finops.request_rollup_hour (
        tenant_ref NVARCHAR(128) NOT NULL,
        bucket_at DATETIME2(0) NOT NULL,
        department_id NVARCHAR(128) NOT NULL,
        workspace_id NVARCHAR(160) NOT NULL,
        agent_id NVARCHAR(128) NOT NULL,
        model_deployment NVARCHAR(160) NOT NULL,
        request_count BIGINT NOT NULL,
        failure_count BIGINT NOT NULL,
        total_tokens BIGINT NULL,
        estimated_cost DECIMAL(20, 10) NULL,
        p50_latency_ms INT NULL,
        p95_latency_ms INT NULL,
        apim_governed_count BIGINT NOT NULL,
        unpriced_count BIGINT NOT NULL,
        refreshed_at DATETIME2(7) NOT NULL
            CONSTRAINT DF_finops_hour_refreshed DEFAULT SYSUTCDATETIME(),
        CONSTRAINT PK_finops_request_rollup_hour PRIMARY KEY (
            tenant_ref, bucket_at, department_id, workspace_id, agent_id, model_deployment
        )
    );
END;

IF OBJECT_ID(N'df_finops.request_rollup_day', N'U') IS NULL
BEGIN
    CREATE TABLE df_finops.request_rollup_day (
        tenant_ref NVARCHAR(128) NOT NULL,
        bucket_at DATE NOT NULL,
        department_id NVARCHAR(128) NOT NULL,
        workspace_id NVARCHAR(160) NOT NULL,
        agent_id NVARCHAR(128) NOT NULL,
        model_deployment NVARCHAR(160) NOT NULL,
        request_count BIGINT NOT NULL,
        failure_count BIGINT NOT NULL,
        total_tokens BIGINT NULL,
        estimated_cost DECIMAL(20, 10) NULL,
        p50_latency_ms INT NULL,
        p95_latency_ms INT NULL,
        apim_governed_count BIGINT NOT NULL,
        unpriced_count BIGINT NOT NULL,
        refreshed_at DATETIME2(7) NOT NULL
            CONSTRAINT DF_finops_day_refreshed DEFAULT SYSUTCDATETIME(),
        CONSTRAINT PK_finops_request_rollup_day PRIMARY KEY (
            tenant_ref, bucket_at, department_id, workspace_id, agent_id, model_deployment
        )
    );
END;

IF OBJECT_ID(N'df_finops.department', N'U') IS NULL
BEGIN
    CREATE TABLE df_finops.department (
        tenant_ref NVARCHAR(128) NOT NULL,
        department_id NVARCHAR(128) NOT NULL,
        display_name NVARCHAR(160) NOT NULL,
        cost_center NVARCHAR(128) NULL,
        status NVARCHAR(16) NOT NULL,
        version INT NOT NULL,
        updated_by NVARCHAR(128) NOT NULL,
        created_at DATETIME2(7) NOT NULL CONSTRAINT DF_finops_department_created DEFAULT SYSUTCDATETIME(),
        updated_at DATETIME2(7) NOT NULL CONSTRAINT DF_finops_department_updated DEFAULT SYSUTCDATETIME(),
        CONSTRAINT PK_finops_department PRIMARY KEY (tenant_ref, department_id),
        CONSTRAINT CK_finops_department_status CHECK (status IN (N'active', N'archived'))
    );
END;

IF COL_LENGTH(N'df_finops.department', N'updated_by') IS NULL
BEGIN
    ALTER TABLE df_finops.department
        ADD updated_by NVARCHAR(128) NOT NULL
            CONSTRAINT DF_finops_department_updated_by DEFAULT N'system';
END;

IF OBJECT_ID(N'df_finops.workspace_department', N'U') IS NULL
BEGIN
    CREATE TABLE df_finops.workspace_department (
        tenant_ref NVARCHAR(128) NOT NULL,
        workspace_id NVARCHAR(160) NOT NULL,
        department_id NVARCHAR(128) NULL,
        version INT NOT NULL,
        updated_at DATETIME2(7) NOT NULL CONSTRAINT DF_finops_workspace_department_updated DEFAULT SYSUTCDATETIME(),
        CONSTRAINT PK_finops_workspace_department PRIMARY KEY (tenant_ref, workspace_id),
        CONSTRAINT FK_finops_workspace_department FOREIGN KEY (tenant_ref, department_id)
            REFERENCES df_finops.department (tenant_ref, department_id)
    );
END;

IF OBJECT_ID(N'df_finops.price_card_revision', N'U') IS NULL
BEGIN
    CREATE TABLE df_finops.price_card_revision (
        tenant_ref NVARCHAR(128) NOT NULL,
        revision_id NVARCHAR(128) NOT NULL,
        revision_status NVARCHAR(24) NOT NULL,
        currency CHAR(3) NOT NULL,
        created_by NVARCHAR(128) NOT NULL,
        reviewed_by NVARCHAR(128) NULL,
        created_at DATETIME2(7) NOT NULL CONSTRAINT DF_finops_price_created DEFAULT SYSUTCDATETIME(),
        reviewed_at DATETIME2(7) NULL,
        activated_at DATETIME2(7) NULL,
        CONSTRAINT PK_finops_price_revision PRIMARY KEY (tenant_ref, revision_id),
        CONSTRAINT CK_finops_price_status CHECK (revision_status IN (N'draft', N'under_review', N'active', N'retired')),
        CONSTRAINT CK_finops_price_currency CHECK (currency = 'USD')
    );
END;

IF OBJECT_ID(N'df_finops.price_card_item', N'U') IS NULL
BEGIN
    CREATE TABLE df_finops.price_card_item (
        tenant_ref NVARCHAR(128) NOT NULL,
        revision_id NVARCHAR(128) NOT NULL,
        model_deployment NVARCHAR(160) NOT NULL,
        input_per_million DECIMAL(20, 8) NULL,
        output_per_million DECIMAL(20, 8) NULL,
        cached_input_per_million DECIMAL(20, 8) NULL,
        reasoning_per_million DECIMAL(20, 8) NULL,
        CONSTRAINT PK_finops_price_item PRIMARY KEY (tenant_ref, revision_id, model_deployment),
        CONSTRAINT FK_finops_price_item_revision FOREIGN KEY (tenant_ref, revision_id)
            REFERENCES df_finops.price_card_revision (tenant_ref, revision_id)
    );
END;

IF OBJECT_ID(N'df_finops.policy', N'U') IS NULL
BEGIN
    CREATE TABLE df_finops.policy (
        tenant_ref NVARCHAR(128) NOT NULL,
        policy_id NVARCHAR(128) NOT NULL,
        policy_type NVARCHAR(64) NOT NULL,
        policy_status NVARCHAR(16) NOT NULL,
        version INT NOT NULL,
        policy_payload NVARCHAR(MAX) NOT NULL,
        created_by NVARCHAR(128) NOT NULL,
        updated_at DATETIME2(7) NOT NULL CONSTRAINT DF_finops_policy_updated DEFAULT SYSUTCDATETIME(),
        CONSTRAINT PK_finops_policy PRIMARY KEY (tenant_ref, policy_id),
        CONSTRAINT CK_finops_policy_json CHECK (ISJSON(policy_payload) = 1),
        CONSTRAINT CK_finops_policy_status CHECK (policy_status IN (N'enabled', N'disabled'))
    );
END;

IF OBJECT_ID(N'df_finops.anomaly', N'U') IS NULL
BEGIN
    CREATE TABLE df_finops.anomaly (
        tenant_ref NVARCHAR(128) NOT NULL,
        anomaly_id NVARCHAR(128) NOT NULL,
        policy_id NVARCHAR(128) NOT NULL,
        workspace_id NVARCHAR(160) NULL,
        severity NVARCHAR(16) NOT NULL,
        anomaly_status NVARCHAR(24) NOT NULL,
        observed_value DECIMAL(20, 8) NULL,
        threshold_value DECIMAL(20, 8) NULL,
        sample_count BIGINT NOT NULL,
        detected_at DATETIME2(7) NOT NULL,
        updated_at DATETIME2(7) NOT NULL,
        details_payload NVARCHAR(MAX) NOT NULL,
        CONSTRAINT PK_finops_anomaly PRIMARY KEY (tenant_ref, anomaly_id),
        CONSTRAINT CK_finops_anomaly_status CHECK (anomaly_status IN (N'open', N'acknowledged', N'suppressed', N'resolved')),
        CONSTRAINT CK_finops_anomaly_severity CHECK (severity IN (N'info', N'warning', N'critical')),
        CONSTRAINT CK_finops_anomaly_json CHECK (ISJSON(details_payload) = 1)
    );
END;

IF OBJECT_ID(N'df_finops.governance_action', N'U') IS NULL
BEGIN
    CREATE TABLE df_finops.governance_action (
        tenant_ref NVARCHAR(128) NOT NULL,
        action_id NVARCHAR(128) NOT NULL,
        action_type NVARCHAR(64) NOT NULL,
        action_status NVARCHAR(32) NOT NULL,
        workspace_id NVARCHAR(160) NULL,
        base_version NVARCHAR(128) NULL,
        proposed_by NVARCHAR(128) NOT NULL,
        approved_by NVARCHAR(128) NULL,
        action_payload NVARCHAR(MAX) NOT NULL,
        result_payload NVARCHAR(MAX) NULL,
        version INT NOT NULL,
        created_at DATETIME2(7) NOT NULL CONSTRAINT DF_finops_action_created DEFAULT SYSUTCDATETIME(),
        updated_at DATETIME2(7) NOT NULL CONSTRAINT DF_finops_action_updated DEFAULT SYSUTCDATETIME(),
        CONSTRAINT PK_finops_governance_action PRIMARY KEY (tenant_ref, action_id),
        CONSTRAINT CK_finops_action_status CHECK (
            action_status IN (
                N'draft', N'pending_approval', N'approved', N'executing', N'verifying',
                N'succeeded', N'failed', N'rolled_back', N'rollback_failed'
            )
        ),
        CONSTRAINT CK_finops_action_json CHECK (ISJSON(action_payload) = 1),
        CONSTRAINT CK_finops_action_result_json CHECK (result_payload IS NULL OR ISJSON(result_payload) = 1)
    );
END;

IF OBJECT_ID(N'df_finops.action_transition', N'U') IS NULL
BEGIN
    CREATE TABLE df_finops.action_transition (
        transition_id BIGINT IDENTITY(1, 1) NOT NULL,
        tenant_ref NVARCHAR(128) NOT NULL,
        action_id NVARCHAR(128) NOT NULL,
        from_status NVARCHAR(32) NULL,
        to_status NVARCHAR(32) NOT NULL,
        actor_ref NVARCHAR(128) NOT NULL,
        reason NVARCHAR(512) NULL,
        evidence_payload NVARCHAR(MAX) NULL,
        occurred_at DATETIME2(7) NOT NULL CONSTRAINT DF_finops_transition_created DEFAULT SYSUTCDATETIME(),
        CONSTRAINT PK_finops_action_transition PRIMARY KEY (transition_id),
        CONSTRAINT FK_finops_transition_action FOREIGN KEY (tenant_ref, action_id)
            REFERENCES df_finops.governance_action (tenant_ref, action_id),
        CONSTRAINT CK_finops_transition_json CHECK (evidence_payload IS NULL OR ISJSON(evidence_payload) = 1)
    );
END;

IF OBJECT_ID(N'df_finops.evidence_alias', N'U') IS NULL
BEGIN
    CREATE TABLE df_finops.evidence_alias (
        tenant_ref NVARCHAR(128) NOT NULL,
        workspace_id NVARCHAR(160) NOT NULL,
        object_kind NVARCHAR(32) NOT NULL,
        object_ref NVARCHAR(256) NOT NULL,
        operation_code NVARCHAR(64) NOT NULL,
        workspace_name_snapshot NVARCHAR(200) NOT NULL,
        display_name NVARCHAR(320) NOT NULL,
        occurred_at DATETIME2(7) NOT NULL,
        created_at DATETIME2(7) NOT NULL CONSTRAINT DF_finops_evidence_alias_created DEFAULT SYSUTCDATETIME(),
        CONSTRAINT PK_finops_evidence_alias PRIMARY KEY (
            tenant_ref, workspace_id, object_kind, object_ref
        ),
        CONSTRAINT CK_finops_evidence_alias_kind CHECK (
            object_kind IN (N'request', N'run', N'trace', N'apim', N'price_revision')
        )
    );
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'df_finops.request_event')
      AND name = N'IX_finops_request_scope_time'
)
BEGIN
    CREATE INDEX IX_finops_request_scope_time
        ON df_finops.request_event (tenant_ref, workspace_id, occurred_at DESC)
        INCLUDE (department_id, agent_id, model_deployment, request_status, total_tokens, cost_amount);
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'df_finops.request_event')
      AND name = N'IX_finops_request_correlation'
)
BEGIN
    CREATE INDEX IX_finops_request_correlation
        ON df_finops.request_event (tenant_ref, correlation_ref)
        WHERE correlation_ref IS NOT NULL;
END;
