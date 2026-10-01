"""Virtual printers: in-memory stand-ins that speak just enough of a vendor's *documented* protocol for the
gate tests to drive the real client code. They encode what the docs say — whether real firmware agrees is what the
separate manual suite in `backend/protocol_verification/` checks."""
