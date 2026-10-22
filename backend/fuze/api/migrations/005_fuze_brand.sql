-- Update the generated welcome project only. User-created projects keep their names.
UPDATE projects
   SET name = 'Welcome to FUZE'
 WHERE is_walkthrough = true AND name = 'Welcome to Continuity';
