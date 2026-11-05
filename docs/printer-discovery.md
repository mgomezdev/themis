# Printer discovery

**Printer plug-in → Settings → Find printers** scans with that enabled plug-in's discovery protocol. The default
`192.168.x.x` covers `192.168.0.0/16`. Narrow it to e.g. `192.168.7.x` for a faster scan; CIDR, dotted subnet masks,
single IPs, and trailing `*` octets also work. Ranges and results in plug-in settings stay in memory. Select **Use**
to fill the Add Printer wizard, then confirm the model and connection before creating a printer. Moonraker firmware
does not always identify the hardware model, so the wizard may ask you to choose it.

**Bambu Lab → Settings → Find printers** uses the same range controls with the Bambu plug-in's own discovery hook.
Printers exposing both LAN MQTT (8883) and FTPS (990) are listed even when SSDP does not announce a model or serial.
Treat these as candidates: confirm the model and enter the serial from the printer's screen, then supply its LAN access
code in the add wizard. Discovery never retrieves the access code.

Choose **Edit details** beside a discovered IP to enter or replace its model and serial number. **Apply details**
updates that result and the Add Printer prefill; **Cancel** discards the draft and **Reset to discovered** restores
the original scan result. A model edit clears the previous automatic model match. Use a known model name (such as
P1S) to select it in the wizard; unrecognized names still require a model choice. Overrides are local to the current
scan and are cleared by rescanning or leaving the page. Nothing is saved as a printer until you finish the add wizard.

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
can route to it and the firewall lets those ports through. Add Printer remembers its range in your browser;
plug-in settings keep it only until you leave the page.

* Ranges must be RFC 1918 (10/8, 172.16/12, 192.168/16), link-local or CGNAT space. Loopback and everything else is refused. A scan covers at most 65,536 addresses in total with 128 workers. Up to 4,096 hosts have a 45-second deadline; larger scans have a 15-minute deadline. Incomplete scans are flagged; narrow the range and retry. Only one scan runs at a time (409 otherwise).
* A passively heard SSDP announcement is attributed to the address it was *sent from*, never the address inside it.
* Leaving the range blank scans this host's own /24 — **inside Docker's default bridge network that is the
  container's network, not your LAN**, so type the printers' range.
* Passive multicast listening needs `network_mode: host` (Linux). Without it the unicast sweep still works.

## Verifying against real printers

`backend/protocol_verification/test_discovery.py` checks each assumption above on real hardware (see that
directory's README). The same checks run in CI against `tests/virtual_printers/virtual_network.py`.
