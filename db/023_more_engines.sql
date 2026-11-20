-- More AI engines for runs and scores: Claude now (Anthropic API with web search), the rest as their collectors arrive.
ALTER TYPE engine_t ADD VALUE IF NOT EXISTS 'claude';
ALTER TYPE engine_t ADD VALUE IF NOT EXISTS 'deepseek';
ALTER TYPE engine_t ADD VALUE IF NOT EXISTS 'google_ai_mode';
ALTER TYPE engine_t ADD VALUE IF NOT EXISTS 'grok';
