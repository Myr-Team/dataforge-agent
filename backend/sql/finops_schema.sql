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

IF OBJECT_ID(N'df_finops.official_price_mapping', N'U') IS NULL
BEGIN
    CREATE TABLE df_finops.official_price_mapping (
        tenant_ref NVARCHAR(128) NOT NULL,
        deployment NVARCHAR(160) NOT NULL,
        official_price_key NVARCHAR(240) NOT NULL,
        mapping_revision INT NOT NULL,
        updated_by_ref NVARCHAR(128) NOT NULL,
        updated_at DATETIME2(7) NOT NULL
            CONSTRAINT DF_finops_official_mapping_updated DEFAULT SYSUTCDATETIME(),
        CONSTRAINT PK_finops_official_price_mapping PRIMARY KEY (
            tenant_ref, deployment
        ),
        CONSTRAINT CK_finops_official_mapping_revision CHECK (
            mapping_revision >= 1
        )
    );
END;

IF OBJECT_ID(N'df_finops.assistant_conversation', N'U') IS NULL
BEGIN
    CREATE TABLE df_finops.assistant_conversation (
        tenant_ref NVARCHAR(128) NOT NULL,
        actor_ref NVARCHAR(128) NOT NULL,
        workspace_id NVARCHAR(160) NOT NULL,
        conversation_ref NVARCHAR(128) NOT NULL,
        title NVARCHAR(120) NOT NULL,
        created_at DATETIME2(7) NOT NULL,
        updated_at DATETIME2(7) NOT NULL,
        expires_at DATETIME2(7) NOT NULL,
        CONSTRAINT PK_finops_assistant_conversation PRIMARY KEY (
            tenant_ref, actor_ref, workspace_id, conversation_ref
        )
    );
    CREATE INDEX IX_finops_assistant_conversation_recent
        ON df_finops.assistant_conversation (
            tenant_ref, actor_ref, workspace_id, updated_at DESC
        );
    CREATE INDEX IX_finops_assistant_conversation_expiry
        ON df_finops.assistant_conversation (expires_at);
END;

IF OBJECT_ID(N'df_finops.assistant_message', N'U') IS NULL
BEGIN
    CREATE TABLE df_finops.assistant_message (
        tenant_ref NVARCHAR(128) NOT NULL,
        actor_ref NVARCHAR(128) NOT NULL,
        workspace_id NVARCHAR(160) NOT NULL,
        conversation_ref NVARCHAR(128) NOT NULL,
        message_id BIGINT IDENTITY(1, 1) NOT NULL,
        role NVARCHAR(16) NOT NULL,
        content NVARCHAR(1600) NOT NULL,
        metric_context_payload NVARCHAR(MAX) NULL,
        created_at DATETIME2(7) NOT NULL,
        CONSTRAINT PK_finops_assistant_message PRIMARY KEY (message_id),
        CONSTRAINT FK_finops_assistant_message_conversation FOREIGN KEY (
            tenant_ref, actor_ref, workspace_id, conversation_ref
        ) REFERENCES df_finops.assistant_conversation (
            tenant_ref, actor_ref, workspace_id, conversation_ref
        ) ON DELETE CASCADE,
        CONSTRAINT CK_finops_assistant_message_role CHECK (
            role IN (N'user', N'assistant')
        ),
        CONSTRAINT CK_finops_assistant_message_context CHECK (
            metric_context_payload IS NULL OR ISJSON(metric_context_payload) = 1
        )
    );
    CREATE INDEX IX_finops_assistant_message_order
        ON df_finops.assistant_message (
            tenant_ref, actor_ref, workspace_id, conversation_ref, message_id
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

IF OBJECT_ID(N'df_finops.insight', N'U') IS NULL
BEGIN
    CREATE TABLE df_finops.insight (
        insight_id NVARCHAR(64) NOT NULL,
        tenant_ref NVARCHAR(128) NOT NULL,
        agent_kind NVARCHAR(16) NOT NULL,
        workspace_scope_hash CHAR(64) NOT NULL,
        trigger_type NVARCHAR(64) NOT NULL,
        trigger_ref NVARCHAR(160) NULL,
        trigger_fingerprint CHAR(64) NOT NULL,
        insight_status NVARCHAR(32) NOT NULL,
        generated_at DATETIME2(7) NOT NULL,
        expires_at DATETIME2(7) NOT NULL,
        insight_payload NVARCHAR(MAX) NOT NULL,
        CONSTRAINT PK_finops_insight PRIMARY KEY (insight_id),
        CONSTRAINT UQ_finops_insight_trigger UNIQUE (
            tenant_ref, agent_kind, trigger_fingerprint
        ),
        CONSTRAINT CK_finops_insight_kind CHECK (
            agent_kind IN (N'finops', N'roi')
        ),
        CONSTRAINT CK_finops_insight_status CHECK (
            insight_status IN (
                N'ready', N'insufficient_data', N'failed', N'stale'
            )
        ),
        CONSTRAINT CK_finops_insight_json CHECK (ISJSON(insight_payload) = 1)
    );
END;

IF OBJECT_ID(N'df_finops.budget', N'U') IS NULL
BEGIN
    CREATE TABLE df_finops.budget (
        tenant_ref NVARCHAR(128) NOT NULL,
        budget_id NVARCHAR(64) NOT NULL,
        name NVARCHAR(160) NOT NULL,
        scope_type NVARCHAR(24) NOT NULL,
        scope_id NVARCHAR(160) NULL,
        period_start DATETIME2(7) NOT NULL,
        period_end DATETIME2(7) NOT NULL,
        amount DECIMAL(19, 8) NOT NULL,
        currency CHAR(3) NOT NULL CONSTRAINT DF_finops_budget_currency DEFAULT 'USD',
        warning_pct DECIMAL(8, 4) NOT NULL,
        critical_pct DECIMAL(8, 4) NOT NULL,
        version INT NOT NULL,
        updated_at DATETIME2(7) NOT NULL CONSTRAINT DF_finops_budget_updated DEFAULT SYSUTCDATETIME(),
        updated_by NVARCHAR(128) NOT NULL,
        CONSTRAINT PK_finops_budget PRIMARY KEY (tenant_ref, budget_id),
        CONSTRAINT CK_finops_budget_scope CHECK (scope_type IN (N'organization', N'department', N'workspace')),
        CONSTRAINT CK_finops_budget_currency CHECK (currency = 'USD'),
        CONSTRAINT CK_finops_budget_period CHECK (period_start < period_end),
        CONSTRAINT CK_finops_budget_amount CHECK (amount > 0)
    );
END;

IF OBJECT_ID(N'df_finops.saved_view', N'U') IS NULL
BEGIN
    CREATE TABLE df_finops.saved_view (
        tenant_ref NVARCHAR(128) NOT NULL,
        view_id NVARCHAR(64) NOT NULL,
        name NVARCHAR(120) NOT NULL,
        audience NVARCHAR(16) NOT NULL,
        portal_tab NVARCHAR(16) NOT NULL,
        filter_payload NVARCHAR(MAX) NOT NULL,
        version INT NOT NULL,
        created_by NVARCHAR(128) NOT NULL,
        updated_at DATETIME2(7) NOT NULL CONSTRAINT DF_finops_view_updated DEFAULT SYSUTCDATETIME(),
        CONSTRAINT PK_finops_saved_view PRIMARY KEY (tenant_ref, view_id),
        CONSTRAINT CK_finops_view_filters CHECK (ISJSON(filter_payload) = 1),
        CONSTRAINT CK_finops_view_audience CHECK (audience IN (N'it', N'finance', N'shared')),
        CONSTRAINT CK_finops_view_tab CHECK (portal_tab IN (N'overview', N'cost', N'roi', N'risk'))
    );
END;

IF OBJECT_ID(N'df_finops.gateway_unmatched_rollup', N'U') IS NULL
BEGIN
    -- Additive, aggregate-only evidence for gateway observations that never
    -- correlate to an application run. It intentionally stores NO correlation
    -- id, body, identity or error body, and is scoped as unattributed/system
    -- so it is never presented as a specific tenant's ledger, error rate or cost.
    CREATE TABLE df_finops.gateway_unmatched_rollup (
        scope NVARCHAR(24) NOT NULL
            CONSTRAINT DF_finops_gateway_unmatched_scope DEFAULT N'unattributed',
        bucket_at DATETIME2(0) NOT NULL,
        status_class NVARCHAR(24) NOT NULL,
        request_count BIGINT NOT NULL,
        data_source NVARCHAR(64) NOT NULL,
        updated_at DATETIME2(7) NOT NULL
            CONSTRAINT DF_finops_gateway_unmatched_updated DEFAULT SYSUTCDATETIME(),
        CONSTRAINT PK_finops_gateway_unmatched_rollup PRIMARY KEY (
            scope, bucket_at, status_class
        ),
        CONSTRAINT CK_finops_gateway_unmatched_scope CHECK (
            scope IN (N'unattributed')
        ),
        CONSTRAINT CK_finops_gateway_unmatched_class CHECK (
            status_class IN (N'client_error_4xx', N'server_error_5xx')
        ),
        CONSTRAINT CK_finops_gateway_unmatched_count CHECK (request_count >= 0)
    );
    CREATE INDEX IX_finops_gateway_unmatched_window
        ON df_finops.gateway_unmatched_rollup (scope, bucket_at);
END;

IF OBJECT_ID(N'df_finops.model_provider', N'U') IS NULL
BEGIN
    CREATE TABLE df_finops.model_provider (
        tenant_ref NVARCHAR(160) NOT NULL,
        provider_id NVARCHAR(80) NOT NULL,
        provider_type NVARCHAR(40) NOT NULL,
        display_name NVARCHAR(120) NOT NULL,
        base_url NVARCHAR(320) NOT NULL,
        region NVARCHAR(32) NULL,
        secret_ref NVARCHAR(240) NOT NULL,
        connection_state NVARCHAR(32) NOT NULL,
        governance_state NVARCHAR(32) NOT NULL,
        available_models_json NVARCHAR(MAX) NOT NULL
            CONSTRAINT DF_finops_model_provider_models DEFAULT N'[]',
        last_tested_at DATETIME2(7) NULL,
        last_success_at DATETIME2(7) NULL,
        safe_error_category NVARCHAR(64) NULL,
        revision INT NOT NULL,
        created_by_ref NVARCHAR(160) NOT NULL,
        updated_by_ref NVARCHAR(160) NOT NULL,
        created_at DATETIME2(7) NOT NULL
            CONSTRAINT DF_finops_model_provider_created DEFAULT SYSUTCDATETIME(),
        updated_at DATETIME2(7) NOT NULL
            CONSTRAINT DF_finops_model_provider_updated DEFAULT SYSUTCDATETIME(),
        CONSTRAINT PK_finops_model_provider PRIMARY KEY (tenant_ref, provider_id),
        CONSTRAINT CK_finops_model_provider_type CHECK (
            provider_type IN (N'deepseek', N'aws_bedrock')
        ),
        CONSTRAINT CK_finops_model_provider_connection CHECK (
            connection_state IN (
                N'testing', N'connected', N'degraded', N'invalid', N'disabled'
            )
        ),
        CONSTRAINT CK_finops_model_provider_governance CHECK (
            governance_state IN (
                N'pending', N'governed', N'degraded', N'unmanaged'
            )
        ),
        CONSTRAINT CK_finops_model_provider_models CHECK (
            ISJSON(available_models_json) = 1
        ),
        CONSTRAINT CK_finops_model_provider_revision CHECK (revision >= 1)
    );
    CREATE UNIQUE INDEX UQ_finops_model_provider_name
        ON df_finops.model_provider (tenant_ref, display_name);
END;

IF COL_LENGTH(N'df_finops.model_provider', N'region') IS NULL
BEGIN
    ALTER TABLE df_finops.model_provider ADD region NVARCHAR(32) NULL;
END;

IF EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = N'CK_finops_model_provider_type'
        AND parent_object_id = OBJECT_ID(N'df_finops.model_provider')
        AND REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
            LOWER(definition), N' ', N''), CHAR(9), N''), CHAR(10), N''),
            CHAR(13), N''), N'[', N''), N']', N'')
            <> N'(provider_typein(n''deepseek'',n''aws_bedrock''))'
)
BEGIN
    ALTER TABLE df_finops.model_provider
        DROP CONSTRAINT CK_finops_model_provider_type;
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = N'CK_finops_model_provider_type'
        AND parent_object_id = OBJECT_ID(N'df_finops.model_provider')
)
BEGIN
    ALTER TABLE df_finops.model_provider ADD CONSTRAINT CK_finops_model_provider_type
        CHECK (provider_type IN (N'deepseek', N'aws_bedrock'));
END;

IF OBJECT_ID(N'df_finops.model_provider_model', N'U') IS NULL
BEGIN
    CREATE TABLE df_finops.model_provider_model (
        tenant_ref NVARCHAR(160) NOT NULL,
        provider_id NVARCHAR(80) NOT NULL,
        model_id NVARCHAR(160) NOT NULL,
        display_name NVARCHAR(200) NOT NULL,
        capability_payload NVARCHAR(MAX) NOT NULL,
        support_state NVARCHAR(32) NOT NULL,
        price_key NVARCHAR(240) NULL,
        observed_at DATETIME2(7) NOT NULL,
        CONSTRAINT PK_finops_model_provider_model PRIMARY KEY (tenant_ref, provider_id, model_id),
        CONSTRAINT FK_finops_model_provider_model_provider FOREIGN KEY (
            tenant_ref, provider_id
        ) REFERENCES df_finops.model_provider (tenant_ref, provider_id),
        CONSTRAINT CK_finops_model_provider_capability CHECK (
            ISJSON(capability_payload) = 1
        ),
        CONSTRAINT CK_finops_model_provider_support CHECK (
            support_state IN (N'supported', N'unsupported', N'unpriced')
        )
    );
END;

IF OBJECT_ID(N'df_finops.provider_route_revision', N'U') IS NULL
BEGIN
    CREATE TABLE df_finops.provider_route_revision (
        tenant_ref NVARCHAR(160) NOT NULL,
        revision_id NVARCHAR(80) NOT NULL,
        workspace_id NVARCHAR(160) NOT NULL,
        revision INT NOT NULL,
        route_payload NVARCHAR(MAX) NOT NULL,
        created_by_ref NVARCHAR(160) NOT NULL,
        created_at DATETIME2(7) NOT NULL
            CONSTRAINT DF_finops_provider_route_created DEFAULT SYSUTCDATETIME(),
        CONSTRAINT PK_finops_provider_route_revision PRIMARY KEY (tenant_ref, revision_id),
        CONSTRAINT CK_finops_provider_route_revision CHECK (revision >= 1),
        CONSTRAINT CK_finops_provider_route_payload CHECK (
            ISJSON(route_payload) = 1
        )
    );
    CREATE UNIQUE INDEX UQ_finops_provider_route_workspace_revision
        ON df_finops.provider_route_revision (tenant_ref, workspace_id, revision);
END;

IF OBJECT_ID(N'df_finops.entra_group_mapping', N'U') IS NULL
BEGIN
    CREATE TABLE df_finops.entra_group_mapping (
        tenant_ref NVARCHAR(160) NOT NULL,
        mapping_id NVARCHAR(80) NOT NULL,
        group_ref NVARCHAR(160) NOT NULL,
        display_name NVARCHAR(200) NOT NULL,
        role_name NVARCHAR(16) NOT NULL,
        workspace_scope_json NVARCHAR(MAX) NOT NULL,
        mapping_priority INT NOT NULL,
        enabled BIT NOT NULL
            CONSTRAINT DF_finops_entra_mapping_enabled DEFAULT 1,
        revision INT NOT NULL,
        created_by_ref NVARCHAR(160) NOT NULL,
        updated_by_ref NVARCHAR(160) NOT NULL,
        created_at DATETIME2(7) NOT NULL
            CONSTRAINT DF_finops_entra_mapping_created DEFAULT SYSUTCDATETIME(),
        updated_at DATETIME2(7) NOT NULL
            CONSTRAINT DF_finops_entra_mapping_updated DEFAULT SYSUTCDATETIME(),
        CONSTRAINT PK_finops_entra_group_mapping PRIMARY KEY (tenant_ref, mapping_id),
        CONSTRAINT CK_finops_entra_mapping_role CHECK (
            role_name IN (N'admin', N'editor', N'viewer')
        ),
        CONSTRAINT CK_finops_entra_mapping_scope CHECK (
            ISJSON(workspace_scope_json) = 1
        ),
        CONSTRAINT CK_finops_entra_mapping_priority CHECK (
            mapping_priority BETWEEN 0 AND 1000
        ),
        CONSTRAINT CK_finops_entra_mapping_revision CHECK (revision >= 1)
    );
    CREATE INDEX IX_finops_entra_mapping_group
        ON df_finops.entra_group_mapping (
            tenant_ref, group_ref, enabled, mapping_priority DESC
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

IF OBJECT_ID(N'df_finops.member_budget', N'U') IS NULL
BEGIN
    CREATE TABLE df_finops.member_budget (
        tenant_ref NVARCHAR(128) NOT NULL,
        budget_id NVARCHAR(64) NOT NULL,
        actor_ref NVARCHAR(128) NOT NULL,
        period_type NVARCHAR(32) NOT NULL,
        amount_usd DECIMAL(19,8) NOT NULL,
        thresholds_json NVARCHAR(256) NOT NULL,
        enabled BIT NOT NULL,
        revision INT NOT NULL,
        created_by_ref NVARCHAR(128) NOT NULL,
        updated_by_ref NVARCHAR(128) NOT NULL,
        created_at DATETIME2(7) NOT NULL,
        updated_at DATETIME2(7) NOT NULL,
        CONSTRAINT PK_finops_member_budget PRIMARY KEY (tenant_ref, budget_id),
        CONSTRAINT CK_finops_member_budget_period CHECK (
            period_type = N'calendar_month_utc'
        ),
        CONSTRAINT CK_finops_member_budget_amount CHECK (amount_usd > 0),
        CONSTRAINT CK_finops_member_budget_thresholds CHECK (
            ISJSON(thresholds_json) = 1
        ),
        CONSTRAINT CK_finops_member_budget_revision CHECK (revision >= 1)
    );
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'df_finops.member_budget')
      AND name = N'UQ_finops_member_budget_active'
)
BEGIN
    CREATE UNIQUE INDEX UQ_finops_member_budget_active
        ON df_finops.member_budget (tenant_ref, actor_ref)
        WHERE enabled = 1;
END;

IF OBJECT_ID(N'df_finops.notification_setting', N'U') IS NULL
BEGIN
    CREATE TABLE df_finops.notification_setting (
        tenant_ref NVARCHAR(128) NOT NULL,
        recipient_actor_ref NVARCHAR(128) NOT NULL,
        recipient_email NVARCHAR(320) NOT NULL,
        sender_display_name NVARCHAR(120) NOT NULL,
        subject_template NVARCHAR(200) NOT NULL,
        body_template NVARCHAR(4000) NOT NULL,
        enabled BIT NOT NULL,
        revision INT NOT NULL,
        created_by_ref NVARCHAR(128) NOT NULL,
        updated_by_ref NVARCHAR(128) NOT NULL,
        created_at DATETIME2(7) NOT NULL,
        updated_at DATETIME2(7) NOT NULL,
        CONSTRAINT PK_finops_notification_setting PRIMARY KEY (tenant_ref),
        CONSTRAINT CK_finops_notification_revision CHECK (revision >= 1)
    );
END;

IF OBJECT_ID(N'df_finops.budget_alert', N'U') IS NULL
BEGIN
    CREATE TABLE df_finops.budget_alert (
        tenant_ref NVARCHAR(128) NOT NULL,
        alert_id NVARCHAR(64) NOT NULL,
        budget_id NVARCHAR(64) NOT NULL,
        actor_ref NVARCHAR(128) NOT NULL,
        period_key CHAR(7) NOT NULL,
        threshold_pct INT NOT NULL,
        budget_amount_usd DECIMAL(19,8) NOT NULL,
        estimated_spend_usd DECIMAL(19,8) NOT NULL,
        pricing_coverage_pct DECIMAL(8,4) NULL,
        budget_revision INT NOT NULL,
        notification_revision INT NOT NULL,
        delivery_state NVARCHAR(16) NOT NULL,
        safe_error_category NVARCHAR(64) NULL,
        attempt_count INT NOT NULL,
        triggered_at DATETIME2(7) NOT NULL,
        sent_at DATETIME2(7) NULL,
        updated_at DATETIME2(7) NOT NULL,
        lease_token NVARCHAR(64) NULL,
        lease_expires_at DATETIME2(7) NULL,
        next_attempt_at DATETIME2(7) NULL,
        CONSTRAINT PK_finops_budget_alert PRIMARY KEY (tenant_ref, alert_id),
        CONSTRAINT UQ_finops_budget_alert_threshold UNIQUE (
            tenant_ref, budget_id, period_key, threshold_pct
        ),
        CONSTRAINT CK_finops_budget_alert_state CHECK (
            delivery_state IN (N'pending', N'sending', N'sent', N'failed', N'suppressed')
        ),
        CONSTRAINT CK_finops_budget_alert_attempt CHECK (
            attempt_count BETWEEN 0 AND 3
        ),
        CONSTRAINT CK_finops_budget_alert_period CHECK (
            period_key LIKE '[0-9][0-9][0-9][0-9]-[0-1][0-9]'
            AND SUBSTRING(period_key, 6, 2) BETWEEN '01' AND '12'
        ),
        CONSTRAINT CK_finops_budget_alert_threshold CHECK (
            threshold_pct BETWEEN 1 AND 100
        ),
        CONSTRAINT CK_finops_budget_alert_lease CHECK (
            (delivery_state = N'sending' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)
            OR
            (delivery_state <> N'sending' AND lease_token IS NULL AND lease_expires_at IS NULL)
        )
    );
END;

IF COL_LENGTH(N'df_finops.budget_alert', N'lease_token') IS NULL
BEGIN
    ALTER TABLE df_finops.budget_alert ADD lease_token NVARCHAR(64) NULL;
END;

IF COL_LENGTH(N'df_finops.budget_alert', N'lease_expires_at') IS NULL
BEGIN
    ALTER TABLE df_finops.budget_alert ADD lease_expires_at DATETIME2(7) NULL;
END;

IF COL_LENGTH(N'df_finops.budget_alert', N'next_attempt_at') IS NULL
BEGIN
    ALTER TABLE df_finops.budget_alert ADD next_attempt_at DATETIME2(7) NULL;
END;

-- A pre-lease worker cannot prove ownership. Recover those rows as due failed
-- claims before installing the invariant; the stable operation ID still
-- protects provider-side retries.
UPDATE df_finops.budget_alert
SET delivery_state = N'failed',
    safe_error_category = COALESCE(safe_error_category, N'service_unavailable'),
    next_attempt_at = COALESCE(next_attempt_at, SYSUTCDATETIME()),
    updated_at = SYSUTCDATETIME()
WHERE delivery_state = N'sending'
  AND (lease_token IS NULL OR lease_expires_at IS NULL);

IF NOT EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = N'CK_finops_budget_alert_lease'
      AND parent_object_id = OBJECT_ID(N'df_finops.budget_alert')
)
BEGIN
    ALTER TABLE df_finops.budget_alert
        ADD CONSTRAINT CK_finops_budget_alert_lease CHECK (
            (delivery_state = N'sending' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)
            OR
            (delivery_state <> N'sending' AND lease_token IS NULL AND lease_expires_at IS NULL)
        );
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'df_finops.budget_alert')
      AND name = N'IX_finops_budget_alert_due'
)
BEGIN
    CREATE INDEX IX_finops_budget_alert_due
        ON df_finops.budget_alert (
            tenant_ref, delivery_state, next_attempt_at, lease_expires_at,
            triggered_at, alert_id
        )
        INCLUDE (attempt_count);
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = N'CK_finops_budget_alert_period'
      AND parent_object_id = OBJECT_ID(N'df_finops.budget_alert')
)
BEGIN
    ALTER TABLE df_finops.budget_alert
        ADD CONSTRAINT CK_finops_budget_alert_period CHECK (
            period_key LIKE '[0-9][0-9][0-9][0-9]-[0-1][0-9]'
            AND SUBSTRING(period_key, 6, 2) BETWEEN '01' AND '12'
        );
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'df_finops.request_event')
      AND name = N'IX_finops_request_actor_window'
)
BEGIN
    CREATE INDEX IX_finops_request_actor_window
        ON df_finops.request_event (tenant_ref, actor_ref, occurred_at)
        INCLUDE (cost_amount, evidence_state);
END;
