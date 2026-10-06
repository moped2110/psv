# Hybrid PQC receipt format v2

Status: phase 1, verification only; 2026-08-14

## Goal and compatibility

Version 2 adds a `sig_v2` field to an existing JSON receipt. It neither replaces nor
alters any v1 field. A v1 verifier may ignore `sig_v2` as an unknown field; v1 receipts
stay verifiable on the existing path with no expiry date. A PQC-capable verifier enables
the new policy only through `PSV_PQC` or the equivalent programmatic configuration. The
default is off. With the flag off, the receipt is neither normalized nor re-serialized,
so the existing path's input and output stay byte-identical.

## Wire format

`sig_v2` has the following closed structure:

```json
{
  "sig_v2": {
    "version": 2,
    "classical": {
      "alg": "ECDSA-P256-SHA256",
      "kid": "facilitator-classical-2026-01",
      "signature": "base64url-without-padding"
    },
    "pqc": {
      "alg": "ML-DSA-65",
      "kid": "facilitator-pqc-2026-01",
      "signature": "base64url-without-padding"
    }
  }
}
```

The versioned contract lives in `src/psv/schemas/pqc-receipt-v2.schema.json`.
Signatures and keys are handled as bytes and encoded in JSON as base64url without
padding. Public keys are not carried in the receipt. `kid` references a pre-trusted,
versioned key inventory; unknown IDs are rejected.

The fixed algorithm registry for v2 contains exactly:

| ID | Use | Parameters |
| --- | --- | --- |
| `ECDSA-P256-SHA256` | classical signature | P-256, SHA-256, DER signature |
| `ML-DSA-65` | post-quantum signature | FIPS 204, pure mode |

Any other ID, in particular look-alike IDs or IDs proposed by the payload, is rejected.
New algorithms require a new format version or an explicit registry extension with an
interoperability review.

## Signed bytes and domain separation

Both signatures cover exactly the same bytes. The whole receipt, with the two
`signature` values blanked, is serialized in a fixed PSV profile: UTF-8, ASCII object
keys sorted lexicographically, no whitespace, no floating-point numbers, decimal amounts
as strings, and no duplicate object keys. Those bytes are prefixed with the ASCII domain
tag `PSV-RECEIPT-V2\x00`. As a result `version`, both `alg` values, both `kid` values and
all business metadata of the receipt are covered jointly by ECDSA and ML-DSA. An attacker
therefore cannot swap the algorithm or the key reference without invalidating both
signatures.

The verifier policy comes from trusted server configuration, never from the payload. In
strict mode the composition is AND: ECDSA **and** ML-DSA-65 must both be valid. A missing
`sig_v2` is a `Naked Receipt`; a field that is present but structurally or
cryptographically unverifiable is an `Unverifiable Receipt`. v2 has no PQC-only mode and
no automatic downgrade.

## Key model

The application resolves `kid` through two separate, operator-controlled registries. Each
ID maps to exactly one algorithm and one public key. Raw public keys are the trust
anchors; PQC X.509 is not part of this format. Rotation adds new IDs. Old public keys
must remain available for as long as old receipts must stay verifiable. Private keys
belong neither in the verifier nor in the repository.

## Providers and failure behaviour

The primary backend is `cryptography` with ML-DSA support from OpenSSL 3.5 or newer. At
runtime the provider checks both the OpenSSL version and the concrete ML-DSA-65 API. If
either is missing, the provider reports a clear unavailability and the tests skip that
backend case instead of crashing at package import. `oqs` is only an optional
`psv[pqc-oqs]` backend and is imported exclusively inside the provider layer.

## Size and latency budget

An ML-DSA-65 signature is 3,309 bytes; base64url needs 4,412 characters for it. With
`version`, algorithm ID, key ID and JSON structure, the additive wire overhead for the
example IDs is 4,697 bytes per receipt (compact JSON, assuming a 72-byte DER ECDSA
signature). The 1,952-byte public key is not sent per receipt because of `kid`. The exact
overhead depends on key-ID lengths and on the v1 fields present; it is a calculation for
the example above, not a figure pinned by a test.

Verification latency depends on hardware and backend, so phase 1 makes no universal
latency number part of any security promise. The recommended measurement is: after a
warm-up, time at least 100 verifications with `time.perf_counter_ns`, sort the individual
latencies, and report p50 and p99 for the specific CPU, `cryptography` and OpenSSL
versions. These are operational data, not part of the wire contract, and no benchmark
script ships with this change. Reference measurement on 2026-08-14: `cryptography 48.0.1`
with OpenSSL 4.0.1, 1,000 ML-DSA-65 verifications after 100 warm-ups on the local CI
worker CPU: p50 0.196 ms, p99 0.468 ms (median and rank 990).

## Threat model and honest limits

The format protects against after-the-fact tampering with a receipt, forgery by an
attacker who can break only one of the two signature families, and stripping or
reinterpreting the PQC metadata while the strict policy is enabled. It does not protect
against compromised issuer keys, tampered key registries, false business statements by an
authorized issuer, or missing long-term time anchoring. For long-term evidential value,
hash anchoring or trusted timestamping remains important.

Chain signatures remain ECDSA/secp256k1 and do not become post-quantum secure because of
this off-chain receipt. A break of the chain cryptography cannot be repaired by `sig_v2`.
Harvest-now-decrypt-later concerns the confidentiality of TLS connections, not receipt
signatures; a hybrid TLS key exchange is a separate measure. This module checks receipt
signature conformance and makes no "quantum-safe payments" claim.

## Migration and rollback

Issuers can roll out `sig_v2` additively while the verifier policy is still off. Once the
key registry is distributed, the policy is enabled deliberately. Rollback needs no data
migration: set `PSV_PQC=off`; v1 and the existing receipt fields stay unchanged. A schema
change requires a new schema version and its own migration note.
