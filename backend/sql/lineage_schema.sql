IF SCHEMA_ID(N'df_lineage') IS NULL
BEGIN
    EXEC(N'CREATE SCHEMA df_lineage AUTHORIZATION dbo');
END;

IF OBJECT_ID(N'df_lineage.workspace_lineage', N'U') IS NULL
BEGIN
    CREATE TABLE df_lineage.workspace_lineage (
        workspace_id NVARCHAR(128) NOT NULL,
        generation INT NOT NULL,
        lifecycle_state NVARCHAR(16) NOT NULL,
        next_version_ordinal INT NOT NULL,
        actor_metadata NVARCHAR(2048) NULL,
        created_at DATETIME2(7) NOT NULL
            CONSTRAINT DF_workspace_lineage_created_at DEFAULT SYSUTCDATETIME(),
        updated_at DATETIME2(7) NOT NULL
            CONSTRAINT DF_workspace_lineage_updated_at DEFAULT SYSUTCDATETIME(),
        row_version ROWVERSION NOT NULL,
        CONSTRAINT PK_workspace_lineage PRIMARY KEY (workspace_id),
        CONSTRAINT CK_workspace_lineage_generation CHECK (generation >= 1),
        CONSTRAINT CK_workspace_lineage_state
            CHECK (lifecycle_state IN (N'active', N'purging', N'purged')),
        CONSTRAINT CK_workspace_lineage_ordinal CHECK (next_version_ordinal >= 1)
    );
END;

IF OBJECT_ID(N'df_lineage.experiment_version', N'U') IS NULL
BEGIN
    CREATE TABLE df_lineage.experiment_version (
        version_id UNIQUEIDENTIFIER NOT NULL,
        workspace_id NVARCHAR(128) NOT NULL,
        generation INT NOT NULL,
        ordinal INT NOT NULL,
        canonical_run_id NVARCHAR(128) NOT NULL,
        decision_fingerprint CHAR(64) NOT NULL,
        evidence_fingerprint CHAR(64) NOT NULL,
        verdict NVARCHAR(64) NULL,
        confidence NVARCHAR(64) NULL,
        actor_metadata NVARCHAR(2048) NULL,
        created_at DATETIME2(7) NOT NULL
            CONSTRAINT DF_experiment_version_created_at DEFAULT SYSUTCDATETIME(),
        CONSTRAINT PK_experiment_version PRIMARY KEY (version_id),
        CONSTRAINT FK_experiment_version_workspace FOREIGN KEY (workspace_id)
            REFERENCES df_lineage.workspace_lineage (workspace_id),
        CONSTRAINT UQ_experiment_version_ordinal
            UNIQUE (workspace_id, generation, ordinal),
        CONSTRAINT UQ_experiment_version_membership
            UNIQUE (version_id, workspace_id, generation),
        CONSTRAINT CK_experiment_version_generation CHECK (generation >= 1),
        CONSTRAINT CK_experiment_version_ordinal CHECK (ordinal >= 1)
    );
END;

IF OBJECT_ID(N'df_lineage.experiment_attachment', N'U') IS NULL
BEGIN
    CREATE TABLE df_lineage.experiment_attachment (
        attachment_id UNIQUEIDENTIFIER NOT NULL,
        version_id UNIQUEIDENTIFIER NOT NULL,
        workspace_id NVARCHAR(128) NOT NULL,
        generation INT NOT NULL,
        kind NVARCHAR(32) NOT NULL,
        source_run_id NVARCHAR(128) NOT NULL,
        payload_sha256 CHAR(64) NOT NULL,
        actor_metadata NVARCHAR(2048) NULL,
        created_at DATETIME2(7) NOT NULL
            CONSTRAINT DF_experiment_attachment_created_at DEFAULT SYSUTCDATETIME(),
        CONSTRAINT PK_experiment_attachment PRIMARY KEY (attachment_id),
        CONSTRAINT FK_experiment_attachment_version
            FOREIGN KEY (version_id, workspace_id, generation)
            REFERENCES df_lineage.experiment_version (version_id, workspace_id, generation),
        CONSTRAINT UQ_experiment_attachment_payload
            UNIQUE (version_id, kind, source_run_id, payload_sha256),
        CONSTRAINT CK_experiment_attachment_generation CHECK (generation >= 1)
    );
END;

IF OBJECT_ID(N'df_lineage.workspace_generation_event', N'U') IS NULL
BEGIN
    CREATE TABLE df_lineage.workspace_generation_event (
        event_id BIGINT IDENTITY(1, 1) NOT NULL,
        workspace_id NVARCHAR(128) NOT NULL,
        generation INT NOT NULL,
        event_kind NVARCHAR(16) NOT NULL,
        actor_metadata NVARCHAR(2048) NULL,
        created_at DATETIME2(7) NOT NULL
            CONSTRAINT DF_workspace_generation_event_created_at DEFAULT SYSUTCDATETIME(),
        CONSTRAINT PK_workspace_generation_event PRIMARY KEY (event_id),
        CONSTRAINT FK_workspace_generation_event_workspace FOREIGN KEY (workspace_id)
            REFERENCES df_lineage.workspace_lineage (workspace_id),
        CONSTRAINT CK_workspace_generation_event_generation CHECK (generation >= 1),
        CONSTRAINT CK_workspace_generation_event_kind
            CHECK (event_kind IN (N'purged', N'recreated'))
    );
END;

IF NOT EXISTS (
    SELECT 1
    FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'df_lineage.experiment_version')
      AND name = N'IX_experiment_version_latest'
)
BEGIN
    CREATE INDEX IX_experiment_version_latest
        ON df_lineage.experiment_version (workspace_id, generation, ordinal DESC);
END;
