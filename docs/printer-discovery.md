# Printer discovery

**Printer plug-in → Settings → Find printers** scans with that enabled plug-in's discovery protocol. The default
`192.168.x.x` covers `192.168.0.0/16`. Narrow it to e.g. `192.168.7.x` for a faster scan; CIDR, dotted subnet masks,
single IPs, and trailing `*` octets also work. **Add printer → Scan network** does the same across every enabled
printer plug-in.

Results stay on the page until the next scan, grouped by plug-in, and each printer is added **in place**:

- A row has a nickname (the scanned name, else e.g. "P1S 30"), a model picker (type to filter; only that plug-in's
  enabled models; required when the scan couldn't tell the model — Moonraker firmware often can't), the IP, and only
  the fields that plug-in asks for on the row (Bambu: serial number and access code; Elegoo and Snapmaker: nothing).
  Values the scan supplied are tagged "from scan". **Test** checks the connection before you add.
- **Add** saves one row; tick rows and **Add N printers** saves several at once (**All new / Complete only / None**
  select quickly). Each row then shows *Added · configured* or *Added · not configured: <reason>*. A row left with
  blank fields is still added — it stays out of the queue until its setup is finished (on the row, or from Fleet's
  *Setup incomplete* card).
- Printers you already have (same IP and model) are listed under **Already in your fleet** with their status;
  **Edit** reopens their fields and saves to the existing printer.
- If a found IP belongs to a printer of a different model, the row warns that the address may have changed, starts
  unticked, and **Edit <name>** lets you move that printer to its new address.
- **More settings…** opens the full add form pre-filled from the row.

A new printer is tested in the background as soon as it is saved; only a successful connection (that test, a **Test**
press, or its first live connection) lets it take queue work. Secrets such as Bambu access codes and Moonraker API
keys are never discovered — you type those. Bambu printers exposing LAN MQTT (8883) and FTPS (990) are listed even
when SSDP announces no model or serial: pick the model and enter the serial from the printer's screen.

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
