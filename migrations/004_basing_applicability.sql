-- Not every model has a base.
--
-- The pipeline makes `Base prepared` and `Based` mandatory for every model. A
-- Rhino has no base, so it either sits at a stage it can never leave or gets
-- advanced through one that never happened — and because every progress figure
-- is effort-weighted, a false advance on an effort-8 vehicle quietly inflates
-- how finished the whole collection looks. Spec §2.5.
--
-- So stages are applicable per model rather than universal, and a model with
-- no base is measured out of five stages instead of seven.
--
-- WHY THIS IS PROPOSED AND NOT DECIDED
--
-- Effort cannot separate them: Rhino, Land Raider, Trukk, Redemptor
-- Dreadnought, Killa Kans and Deff Dread are all effort 8. Keywords look like
-- they can — measured against the imported 1,445:
--
--     Vehicle + Walker    Redemptor Dreadnought, Killa Kans, Deff Dread   base
--     Vehicle             Rhino, Land Raider, Trukk, Predator, Battlewagon  none
--
-- That is a real signal and it is why the keywords are now stored. It is not
-- a licence to classify 1,445 datasheets automatically. Whether a kit ships
-- with a base is a fact about the plastic; BSData describes the rules, and the
-- correlation above is nine models checked by hand, not a rule GW publishes.
-- One wrong classification is silent and inflates progress in the direction
-- that flatters.
--
-- So: NULL by default, meaning nobody has said, behaving exactly as today.
-- The keyword signal surfaces as a *hint* Clay confirms — the same bargain the
-- app already makes with box contents, which are pre-filled and never
-- auto-saved. He marks the handful of vehicles he actually owns,
-- opportunistically, the way paint stages get corrected.
ALTER TABLE datasheets ADD COLUMN basing TEXT
  CHECK (basing IN ('based', 'unbased'));

-- Read at import time for the effort heuristic and then thrown away, which is
-- why nothing could ask "is this a Vehicle" after the fact. Stored as a JSON
-- array, and now load-bearing for the basing hint above.
ALTER TABLE datasheets ADD COLUMN keywords TEXT;

-- Which stages basing applicability governs, marked in the data rather than
-- matched by name in Python. Renaming a stage should not silently detach the
-- rule from it.
ALTER TABLE stages ADD COLUMN is_basing INTEGER NOT NULL DEFAULT 0;

UPDATE stages SET is_basing = 1 WHERE name IN ('Base prepared', 'Based');
