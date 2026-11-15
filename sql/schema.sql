CREATE TABLE IF NOT EXISTS tasks (
    id UUID PRIMARY KEY,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'processing', 'done', 'failed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agent_results (
    id UUID PRIMARY KEY,
    task_id UUID NOT NULL REFERENCES tasks (id) ON DELETE CASCADE,
    agent_name TEXT NOT NULL,
    result JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS policies (
    id UUID PRIMARY KEY,
    action TEXT NOT NULL UNIQUE,
    effect TEXT NOT NULL CHECK (effect IN ('ALLOW', 'DENY', 'REQUIRE_APPROVAL')),
    max_risk TEXT NOT NULL DEFAULT 'low',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit_events (
    id UUID PRIMARY KEY,
    task_id UUID REFERENCES tasks (id) ON DELETE SET NULL,
    event_type TEXT NOT NULL,
    details JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS tasks_status_idx ON tasks (status);
CREATE INDEX IF NOT EXISTS agent_results_task_idx ON agent_results (task_id);
CREATE INDEX IF NOT EXISTS audit_events_task_idx ON audit_events (task_id);

INSERT INTO policies (id, action, effect, max_risk)
VALUES
    ('6f0c1a10-7c2e-4a1b-9d11-0a1b2c3d4e01', 'record_only', 'ALLOW', 'low'),
    ('6f0c1a10-7c2e-4a1b-9d11-0a1b2c3d4e02', 'block_host', 'REQUIRE_APPROVAL', 'high'),
    ('6f0c1a10-7c2e-4a1b-9d11-0a1b2c3d4e03', 'isolate_host', 'REQUIRE_APPROVAL', 'high')
ON CONFLICT (action) DO UPDATE
SET effect = EXCLUDED.effect,
    max_risk = EXCLUDED.max_risk;
