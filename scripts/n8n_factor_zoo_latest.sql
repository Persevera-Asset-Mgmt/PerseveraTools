-- n8n workflow "Factor Zoo - Atualizar Latest", node "Atualizar Factor Zoo Latest".
-- Paste as the node query; set Options > Query Parameters to: 400  (window in days, $1).
--
-- factor_zoo_latest used to be upsert-only, so rows removed from factor_zoo and
-- dead codes (e.g. momentum = 0 dated "today" for companies delisted years ago)
-- stayed forever: 11,176 orphan rows / 745 codes on 2026-10-06. This statement
-- also deletes (code, field) pairs without a value in the window.

WITH src AS (
    SELECT DISTINCT ON (code, field)
           code,
           field,
           date,
           value
    FROM factor_zoo
    WHERE date >= CURRENT_DATE - $1::int
      AND value IS NOT NULL
      AND lower(field) <> 'announcement_dt'
    ORDER BY code, field, date DESC
),
purged AS (
    -- (code, field) without a value in the window: dead code or row gone from factor_zoo
    DELETE FROM factor_zoo_latest t
    WHERE NOT EXISTS (
        SELECT 1 FROM src s WHERE s.code = t.code AND s.field = t.field
    )
)
INSERT INTO factor_zoo_latest AS t (code, field, date, value)
SELECT code, field, date, value FROM src
ON CONFLICT (code, field) DO UPDATE
   SET date  = EXCLUDED.date,
       value = EXCLUDED.value
 WHERE t.date  IS DISTINCT FROM EXCLUDED.date
    OR t.value IS DISTINCT FROM EXCLUDED.value;
