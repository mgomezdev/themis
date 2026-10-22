"""Provider interfaces: the seam between Themis core and external systems (Laminus; inventory systems are plugins, see app/plugins).

Core code talks to the ABCs and neutral DTOs here; only the adapter packages under
`providers/<adapter>/` may import the vendor clients. Same pattern as `AbstractPrinterClient`:
an ABC, capability flags, and a registry/accessor.
"""
