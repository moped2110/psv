"""A bundled reference System-under-Test (SUT): a complete x402 payment system.

It is intentionally realistic *and* intentionally fragile in one specific way:
it confirms settlement by watching the token's ``Transfer`` event signature — a
common, plausible pattern that SC1 (ABI / event drift) silently breaks. The
harness tests *against* this SUT to prove it can catch that failure; Mario's own
system later plugs into the same HTTP adapter contract.
"""
