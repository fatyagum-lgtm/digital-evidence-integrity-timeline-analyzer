# Digital Forensics Investigation Checklist

This checklist provides recommended practices for using the Digital Evidence Integrity & Timeline Analyzer in educational forensic workflows.

## Before Analysis
- [ ] Confirm that the investigation is authorized.
- [ ] Identify evidence sources and investigation scope.
- [ ] Preserve original files.
- [ ] Record collection environment details.
- [ ] Prepare a separate analysis workspace.

## Evidence Integrity
- [ ] Record SHA-256 hashes of collected files.
- [ ] Document when and how hashes were computed.
- [ ] Compare hashes when verifying integrity.
- [ ] Investigate unexpected integrity differences.
- [ ] Keep original evidence separate from reports.

## Timeline Analysis
- [ ] Review filesystem timestamps.
- [ ] Record timezone assumptions.
- [ ] Consider operating system and filesystem differences.
- [ ] Correlate events with other available evidence.
- [ ] Avoid treating timestamps as definitive proof.

## Reporting
- [ ] Document analysis steps and tool versions.
- [ ] Separate observations from interpretations.
- [ ] Explain limitations and unresolved findings.
- [ ] Preserve generated reports.

## Limitations

This checklist is educational guidance. It does not replace validated forensic procedures, formal chain-of-custody documentation, or qualified forensic expertise.
