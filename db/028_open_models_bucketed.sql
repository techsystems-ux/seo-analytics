-- Testing phase: Google's open model (Gemma) answers stand in for Gemini and Microsoft's (Phi) for Copilot, under those
-- engines' own keys, so the screens keep one button per real engine. DeepSeek is dropped (no such engine on the screens).
SELECT set_config('app.is_staff', 'true', true);
UPDATE runs SET engine = 'gemini' WHERE engine = 'gemma';
UPDATE runs SET engine = 'bing_copilot' WHERE engine = 'phi';
DELETE FROM runs WHERE engine = 'deepseek';
DELETE FROM scores_daily WHERE engine IN ('gemma', 'phi', 'deepseek');
DELETE FROM score_versions;              -- re-score every brand on next open, under the new keys
