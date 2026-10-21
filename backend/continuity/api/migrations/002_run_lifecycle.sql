-- A lease prevents duplicate execution across requests and rolling deployments.
ALTER TABLE threads ADD COLUMN IF NOT EXISTS run_token text;
ALTER TABLE threads ADD COLUMN IF NOT EXISTS lease_until timestamptz;
CREATE INDEX IF NOT EXISTS threads_active_lease_idx ON threads (lease_until)
    WHERE status = 'running';

-- Enforce the denormalized ownership relationship at the database boundary.
CREATE UNIQUE INDEX IF NOT EXISTS projects_id_user_idx ON projects (id, user_id);
ALTER TABLE threads ADD CONSTRAINT threads_project_owner_fk
    FOREIGN KEY (project_id, user_id) REFERENCES projects (id, user_id) ON DELETE CASCADE;
CREATE UNIQUE INDEX IF NOT EXISTS threads_id_project_user_idx ON threads (id, project_id, user_id);
ALTER TABLE findings ADD CONSTRAINT findings_thread_owner_fk
    FOREIGN KEY (thread_id, project_id, user_id)
    REFERENCES threads (id, project_id, user_id) ON DELETE CASCADE;

ALTER TABLE run_events ADD CONSTRAINT run_events_sequence_check CHECK (seq >= 0);
ALTER TABLE projects ADD CONSTRAINT projects_name_check CHECK (length(trim(name)) BETWEEN 1 AND 200);
