-- K1-B: accept Origin Energy PDF reports. Originals stay in the private object adapter.
ALTER TABLE markos_control.bill_records DROP CONSTRAINT bill_records_report_check;
ALTER TABLE markos_control.bill_records ADD CONSTRAINT bill_records_report_check CHECK((
 report->>'source_sha256'=source_sha256 AND jsonb_typeof(report->'facts')='array'
 AND ((report->>'extractor'='bill-text-v1' AND jsonb_array_length(report->'facts')=6)
  OR (report->>'extractor'='amber-pdf-v1' AND jsonb_array_length(report->'facts')=15)
  OR (report->>'extractor'='origin-pdf-v1' AND jsonb_array_length(report->'facts')=13))) IS TRUE);
