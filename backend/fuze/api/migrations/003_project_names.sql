ALTER TABLE projects ADD COLUMN IF NOT EXISTS name_source text NOT NULL DEFAULT 'manual'
    CHECK (name_source IN ('manual', 'generated'));
