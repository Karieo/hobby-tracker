-- Where a barcode's link to a kit template came from.
--
-- Two very different kinds of evidence end up in this table, and telling them
-- apart matters because one is much stronger than the other:
--
--   'seed'    two independent published sources agreeing, per the catalogue's
--             provenance rules. Good, but it is somebody's web page.
--   'scanned' Clay held the physical box, scanned it, and said what it was.
--             That is the strongest evidence there is — the plastic in his
--             hands — and it is the only way most codes will ever be learned.
--
-- The measured reason this column exists: retailers publish the Games Workshop
-- product code (103-48) everywhere and the EAN almost nowhere, so research can
-- supply a box's *contents* but not its *barcode*. Scanning supplies the
-- barcode. The catalogue and the scanner each know half, and this records
-- which half a given row came from so a later correction knows what it is
-- overruling.
ALTER TABLE barcodes ADD COLUMN link_source TEXT
  CHECK (link_source IN ('seed', 'scanned', 'manual'));

-- Rows that already carry a template were linked by the seed importer or by
-- the barcode form on the template page; neither was a scan.
UPDATE barcodes SET link_source = 'seed' WHERE kit_template_id IS NOT NULL;
