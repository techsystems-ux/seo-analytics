-- Prompts chosen from search demand (collectors/prompt_builder.py): where each came from and its topic's monthly searches.
ALTER TABLE prompts ADD COLUMN source text NOT NULL DEFAULT 'team',     -- team | semrush | semrush_gap | gsc | keyword_planner
                    ADD COLUMN source_keyword text,
                    ADD COLUMN search_volume integer;                  -- monthly searches of the prompt's topic, for the score's demand weight
