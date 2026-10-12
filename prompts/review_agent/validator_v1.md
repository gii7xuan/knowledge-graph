# Triple validator v1

Judge the complete candidate against only the supplied source-document evidence and ontology checks.
Return structured JSON. Use only supplied `evidence_id` values. Missing evidence means `uncertain`, not `invalid`.
Check subject, relation, object, direction, polarity, conditions, quantities, time scope, references, and schema constraints.
