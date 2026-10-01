# Printer discovery

**Add printer → Scan network** sweeps one or more IP ranges and lists the printers that answer to their vendor's
documented discovery signature. Choosing one pre-fills the add form (type, nickname, IP, serial / port); secrets such
as Bambu access codes and Moonraker API keys are never discovered — you type those.

## How it finds printers

Each client class implements `discover_host(net, ip)` (see `AbstractPrinterClient`); the scan calls every vendor on
every address of the range:

| Vendor | Signature (documented protocol) | Gives |
|---|---|---|
| Bambu Lab (LAN mode) | TCP **8883** (MQTT) and **990** (FTPS) both open; unicast SSDP `M-SEARCH` (`ST: urn:bambulab-com:device:3dprinter:1`) to UDP **1990** | serial (`USN`), model code → name, device name |
| Elegoo Centauri | UDP **3000** `M99999` → SDCP JSON | name, machine, mainboard id, reported IP |
| Moonraker / Snapmaker U1 | `GET :7125/server/info` (`moonraker_version`); 401/403 = needs an API key | hostname via `/printer/info` |

Same-subnet Bambu printers that announce themselves on the SSDP multicast group (UDP 1990 / 2021) are merged in
when the server can receive multicast.

## Other VLANs and Docker

Multicast and broadcast never cross routers, so discovery **sweeps addresses with unicast probes** instead: give the
printers' range (e.g. Themis on `192.168.3.15`, printers on `192.168.7.0/24`) and it works as long as the Themis host
can route to it and the firewall lets those ports through. The range is remembered in your browser.

* Ranges must be private (RFC 1918, CGNAT or link-local) and at most a /20; a scan stops after 45 s and says so.
* Leaving the range blank scans this host's own /24 — **inside Docker's default bridge network that is the
  container's network, not your LAN**, so type the printers' range.
* Passive multicast listening needs `network_mode: host` (Linux). Without it the unicast sweep still works.

## Verifying against real printers

`backend/protocol_verification/test_discovery.py` checks each assumption above on real hardware (see that
directory's README). The same checks run in CI against `tests/virtual_printers/virtual_network.py`.
