CREATE TABLE design_revisions (
    id text PRIMARY KEY,
    project_id text NOT NULL,
    user_id text NOT NULL,
    thread_id text NOT NULL,
    parent_id text REFERENCES design_revisions(id),
    content_hash text NOT NULL,
    document jsonb NOT NULL,
    reason text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (id, project_id, user_id),
    UNIQUE (thread_id, content_hash),
    FOREIGN KEY (thread_id, project_id, user_id) REFERENCES threads(id, project_id, user_id) ON DELETE CASCADE
);
CREATE INDEX design_revisions_project_idx ON design_revisions(project_id, created_at DESC);
ALTER TABLE threads ADD COLUMN revision_id text REFERENCES design_revisions(id) ON DELETE SET NULL;
ALTER TABLE projects ADD COLUMN revision_id text REFERENCES design_revisions(id) ON DELETE SET NULL;

CREATE TABLE run_inputs (
    thread_id text PRIMARY KEY REFERENCES threads(id) ON DELETE CASCADE,
    kind text NOT NULL CHECK (kind IN ('brief', 'bom')),
    payload jsonb NOT NULL,
    automatic_retries integer NOT NULL DEFAULT 0,
    cancelled boolean NOT NULL DEFAULT false
);
CREATE TABLE run_stages (
    thread_id text NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    stage_key text NOT NULL,
    result jsonb NOT NULL,
    completed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (thread_id, stage_key)
);
CREATE TABLE research_requests (
    thread_id text NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    request_key text NOT NULL,
    status text NOT NULL CHECK (status IN ('reserved', 'complete', 'failed')),
    result jsonb,
    cost_usd numeric NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (thread_id, request_key)
);
