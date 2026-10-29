"""Data connectors (trusted side). Each data need has a typed contract; adapters fetch from a source and return it.
Real sources are reached only through `net.safe_http`; SAP-side systems are fail-closed stubs until credentials and
endpoints exist. See docs/03-connectors.md."""
