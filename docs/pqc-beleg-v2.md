# PQC-Belegformat v2

Status: Phase 1, Verifikation; 2026-08-14

## Ziel und Kompatibilität

Version 2 ergänzt einen bestehenden JSON-Beleg um das Feld `sig_v2`. Es ersetzt und
verändert kein v1-Feld. Ein v1-Verifier darf `sig_v2` als unbekanntes Feld ignorieren;
v1-Belege bleiben ohne Ablaufdatum mit dem bisherigen Pfad verifizierbar. Ein
PQC-fähiger Verifier aktiviert die neue Policy ausschließlich über `PSV_PQC` oder die
gleichwertige Programmkonfiguration. Der Default ist aus. Bei ausgeschaltetem Flag wird
der Beleg weder normalisiert noch neu serialisiert; Eingabe und Ausgabe des bestehenden
Pfads bleiben deshalb byte-identisch.

## Wire-Format

`sig_v2` hat folgende geschlossene Struktur:

```json
{
  "sig_v2": {
    "version": 2,
    "classical": {
      "alg": "ECDSA-P256-SHA256",
      "kid": "facilitator-classical-2026-01",
      "signature": "base64url-ohne-padding"
    },
    "pqc": {
      "alg": "ML-DSA-65",
      "kid": "facilitator-pqc-2026-01",
      "signature": "base64url-ohne-padding"
    }
  }
}
```

Der versionierte Vertrag liegt in `src/psv/schemas/pqc-receipt-v2.schema.json`.
Signaturen und Schlüssel werden binär verarbeitet und im JSON als Base64url ohne
Padding codiert. Öffentliche Schlüssel stehen nicht im Beleg. `kid` referenziert einen
vorab vertrauten, versionierten Schlüsselbestand; unbekannte IDs werden abgelehnt.

Die feste Algorithmus-Registry für v2 enthält ausschließlich:

| ID | Verwendung | Parameter |
| --- | --- | --- |
| `ECDSA-P256-SHA256` | klassische Signatur | P-256, SHA-256, DER-Signatur |
| `ML-DSA-65` | Post-Quantum-Signatur | FIPS 204, reiner Modus |

Andere IDs, insbesondere ähnlich geschriebene oder vom Payload vorgeschlagene IDs,
werden abgelehnt. Neue Algorithmen erfordern eine neue Formatversion oder eine explizite
Registry-Erweiterung mit Interoperabilitätsprüfung.

## Signierte Bytes und Domain Separation

Beide Signaturen decken exakt dieselben Bytes ab. Dazu wird der gesamte Beleg ohne die
beiden `signature`-Werte als kanonisches JSON nach RFC 8785 serialisiert. Diese Bytes
werden mit dem ASCII-Präfix `PSV-RECEIPT-V2\x00` verbunden. Damit sind insbesondere
`version`, beide `alg`-Werte, beide `kid`-Werte und sämtliche fachlichen
Belegmetadaten von ECDSA und ML-DSA gemeinsam abgedeckt. Ein Angreifer kann daher weder
Algorithmus noch Schlüsselreferenz austauschen, ohne beide Signaturen ungültig zu
machen.

Die Verifier-Policy stammt aus vertrauenswürdiger Serverkonfiguration, nie aus dem
Payload. Im Strict-Modus gilt AND-Komposition: ECDSA **und** ML-DSA-65 müssen gültig
sein. Ein fehlendes `sig_v2` ist ein `Naked Receipt`; ein vorhandenes, aber strukturell
oder kryptografisch nicht prüfbares Feld ist ein `Unverifiable Receipt`. Es gibt in v2
keinen puren PQC-Modus und keinen automatischen Downgrade.

## Schlüsselmodell

Die Anwendung löst `kid` über zwei getrennte, vom Betreiber kontrollierte Registries
auf. Eine ID ist genau einem Algorithmus und einem öffentlichen Schlüssel zugeordnet.
Raw Public Keys sind Vertrauensanker; PQC-X.509 ist nicht Teil dieses Formats. Rotation
fügt neue IDs hinzu. Alte öffentliche Schlüssel müssen für die geforderte
Aufbewahrungsdauer alter Belege verfügbar bleiben. Private Schlüssel gehören weder in
den Verifier noch in das Repository.

## Provider und Ausfallverhalten

Das primäre Backend ist `cryptography` mit ML-DSA-Unterstützung durch OpenSSL 3.5 oder
neuer. Die Laufzeit prüft sowohl Version als auch die konkrete ML-DSA-65-API. Fehlt sie,
meldet der Provider eine klare Nichtverfügbarkeit; Tests überspringen den Backend-Fall,
statt beim Import des Pakets abzustürzen. `oqs` ist nur ein optionales
`psv[pqc-oqs]`-Backend und wird ausschließlich innerhalb der Provider-Schicht
importiert.

## Größen- und Laufzeitbudget

Eine ML-DSA-65-Signatur ist 3.309 Byte groß. Base64url benötigt dafür 4.412 Zeichen;
mit `version`, Algorithmus-ID, Key-ID und JSON-Struktur beträgt der additive
Wire-Overhead bei den Beispiel-IDs rund 4,55 KB je Beleg. Der 1.952-Byte-Public-Key
wird wegen `kid` nicht je Beleg übertragen. Der exakte Overhead hängt von den
Key-ID-Längen und vorhandenen v1-Feldern ab und wird im Test reproduzierbar gemessen.

Verify-Latenzen sind hardware- und Backend-abhängig. Phase 1 legt deshalb keinen
universellen Zahlenwert als Sicherheitsversprechen fest: Der mitgelieferte Benchmark
misst nach Warm-up mindestens 100 Verifikationen und berichtet p50/p99 für die konkrete
CPU-, `cryptography`- und OpenSSL-Version. Diese Werte sind Betriebsdaten, kein Teil des
Wire-Vertrags.

## Threat Model und ehrliche Grenze

Das Format schützt gegen nachträgliche Manipulation eines Belegs, Fälschung durch einen
Angreifer, der nur eine der beiden Signaturfamilien brechen kann, sowie das Strippen oder
Umdeuten der PQC-Metadaten bei aktivierter Strict-Policy. Es schützt nicht gegen
kompromittierte Aussteller-Schlüssel, manipulierte Key-Registries, falsche fachliche
Angaben eines autorisierten Ausstellers oder fehlende Langzeit-Zeitverankerung. Für
langfristige Beweiskraft bleibt Hash-Anchoring beziehungsweise vertrauenswürdiges
Timestamping wichtig.

Chain-Signaturen bleiben ECDSA/secp256k1 und werden durch diesen Off-Chain-Beleg nicht
post-quanten-sicher. Ein Bruch der Chain-Kryptografie kann durch `sig_v2` nicht repariert
werden. Harvest-now-decrypt-later betrifft die Vertraulichkeit von TLS-Verbindungen,
nicht Belegsignaturen; ein hybrider TLS-Key-Exchange ist eine getrennte Maßnahme. Dieses
Modul prüft Beleg-Signatur-Konformität und macht keine „quantum-safe payments“-Aussage.

## Migration und Rollback

Aussteller können `sig_v2` additiv ausrollen, während die Verifier-Policy noch aus ist.
Nach verteilter Schlüsselregistry wird die Policy gezielt aktiviert. Rollback erfolgt
ohne Datenmigration durch `PSV_PQC=off`; v1 und die vorhandenen Belegfelder bleiben
unverändert. Ein Schemawechsel erfordert eine neue Schemaversion und eine eigene
Migrationsnotiz.
