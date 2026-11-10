# Coverage audit before full ranking experiments

42/49 legacy judged destination IDs have knowledge chunks; all 49 remain in metric denominators. This is a coverage audit, not a claim about model quality. The archived dump was inspected directly; no new API requests or ingestion changes were made. `coverage-audit.json` records page/revision IDs and geo templates.

| Judged destination | Observed gap | Evidence / limitation |
|---|---|---|
| Cusco (3941584) | Identity/alias matching | Catalog name Cusco; the Cusco guide is a redirect, active Cuzco guide revision 5400925 has a geo point but does not pass current exact-name matching. |
| Fes (2548885) | Identity/alias matching | Catalog name Fes; Fes is a redirect, active Fez guide revision 5402008 has a geo point but fails current matching. |
| Marrakesh (2542997) | Identity/alias matching | Catalog name Marrakesh; active Marrakech guide revision 5410691 has a geo point; Marrakesh is a redirect. |
| Nara (1855612) | Identity/name matching | Catalog name Nara-shi; Nara guide revision 5412288 has a geo point but fails current matching. |
| Banff (5892532) | Canonical catalog coverage | Requested GeoNames ID absent from imported catalog; a Banff guide with coordinates exists (revision 5394387). |
| Zermatt (2657928) | Canonical catalog coverage | Requested GeoNames ID absent from imported catalog; a Zermatt guide with coordinates exists (revision 5301846). |
| Queenstown (2184868) | Canonical catalog coverage, guide ambiguity | Requested ID absent from imported catalog. Root Queenstown page has no geo template; this does not establish whether a disambiguated destination guide exists. |

These unavoidable coverage misses affect mountain-hiking, historic-photo, temples, winter and markets queries. They are not proof that the relevant city is absent from Wikivoyage. Alias reconciliation/catalog expansion is future work, not silently fixed or removed from evaluation here. At least ten query-level ranking failures still require inspection after full-corpus results are available; the preflight coverage audit does not substitute for that analysis.
