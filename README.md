<div align="center">

# Network Discovery for Prometheus

**Automated network asset discovery, inventory enrichment, and observability target generation.**

[![Python](https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Prometheus](https://img.shields.io/badge/Prometheus-file__sd-E6522C?logo=prometheus&logoColor=white)](https://prometheus.io/docs/prometheus/latest/configuration/configuration/#file_sd_config)
[![Telegraf](https://img.shields.io/badge/Telegraf-supported-0F62FE?logo=influxdb&logoColor=white)](https://www.influxdata.com/time-series-platform/telegraf/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

Discover devices once. Enrich them consistently. Monitor them automatically.

</div>

---

## Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Capabilities](#capabilities)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Running a discovery](#running-a-discovery)
- [Generated artifacts](#generated-artifacts)
- [Prometheus integration](#prometheus-integration)
- [Telegraf integration](#telegraf-integration)
- [Security and operations](#security-and-operations)
- [Troubleshooting](#troubleshooting)
- [Roadmap](#roadmap)
- [Contributing](#contributing)

## Overview

`network-discovery-prometheus` is a lightweight Python discovery runner for Linux networks. It combines Nmap host discovery, ARP/MAC resolution, DNS and mDNS lookup, latency checks, service inspection, operating-system detection, and optional SNMP identification into a durable inventory.

The same run produces Prometheus `file_sd_config` targets and a Telegraf ping configuration, allowing newly discovered devices to become observable without manually maintaining target lists.

> **Authorization:** run this only on networks you own or are explicitly authorized to assess. Network scanning can be intrusive and may trigger security controls.

## Architecture

The diagrams below intentionally use plain-text boxes instead of Mermaid. This keeps the README compatible with GitHub renderers and avoids Mermaid rich-display errors.

### End-to-end data flow

```text
┌──────────────────────┐
│ Authorized network   │
│ servers · switches   │
│ routers · IoT · PCs  │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────┐
│ discovery.py                                             │
│                                                          │
│  1. Load config, dictionaries, and history               │
│  2. Nmap host discovery (-sn -R)                         │
│  3. ARP scans and MAC/vendor enrichment                  │
│  4. Parallel host processing (20 workers)                │
│  5. Ping, DNS/mDNS, ports, services, OS, and SNMP        │
└──────────┬───────────────────┬───────────────────┬───────┘
           │                   │                   │
           ▼                   ▼                   ▼
┌──────────────────┐  ┌────────────────────┐  ┌─────────────────────┐
│ inventory.json   │  │ history.json       │  │ discovery.log       │
│ Enriched assets  │  │ MAC/IP history     │  │ Rotating operations │
└──────────────────┘  └────────────────────┘  └─────────────────────┘
           │
           ├──────────────────────────────┐
           ▼                              ▼
┌──────────────────────────┐   ┌──────────────────────────┐
│ Prometheus targets       │   │ Telegraf configuration   │
│ /etc/prometheus/         │   │ /etc/telegraf/           │
│ targets/rede.json        │   │ telegraf.d/ping.conf     │
└────────────┬─────────────┘   └────────────┬─────────────┘
             ▼                              ▼
      ┌──────────────┐                 ┌──────────────┐
      │ Prometheus   │                 │ Telegraf     │
      │ file_sd      │                 │ inputs.ping  │
      └──────┬───────┘                 └──────┬───────┘
             └──────────────┬─────────────────┘
                            ▼
                    ┌──────────────┐
                    │ Grafana and  │
                    │ alerting     │
                    └──────────────┘
```

### Discovery sequence

```text
START
  │
  ├─ Load config.json and optional lookup dictionaries
  ├─ Run Nmap host discovery
  ├─ Run up to three arp-scan passes
  ├─ Submit each host to a 20-worker thread pool
  │    ├─ Resolve MAC and friendly name
  │    ├─ Resolve DNS/mDNS name
  │    ├─ Measure ICMP latency
  │    ├─ Detect open ports and services
  │    ├─ Detect operating system and device type
  │    └─ Query SNMP sysDescr
  ├─ Update MAC-based history
  ├─ Write inventory.json, history.json, and rede.json
  ├─ Write Telegraf ping inputs
  └─ Restart Telegraf
  │
END
```

## Capabilities

| Area | What it does |
| --- | --- |
| **Host discovery** | Uses Nmap ping discovery with reverse DNS resolution. |
| **Layer-2 identity** | Resolves MAC addresses through `arp-scan`, including retries. |
| **Enrichment** | Collects hostname, vendor, latency, OS, device type, open TCP ports, services, and SNMP `sysDescr`. |
| **Classification** | Applies MAC/name, vendor, and device-type dictionaries. |
| **Concurrency** | Processes hosts with `ThreadPoolExecutor(max_workers=20)`. |
| **History** | Tracks first/last observation and known IP addresses by MAC address. |
| **Observability** | Generates Prometheus file-based discovery targets and Telegraf ping inputs. |
| **Operations** | Writes rotating logs and restarts Telegraf after generation. |

## Repository layout

```text
.
├── discovery.py   # Discovery, enrichment, persistence, and integrations
└── README.md      # Architecture and operating guide
```

Runtime data files are supplied or created during deployment: `config.json`, lookup dictionaries, inventory/history output, Prometheus targets, Telegraf configuration, and logs.

## Requirements

- Linux host with access to the target network.
- Python 3.9+ recommended.
- Permissions to perform ARP/Nmap inspection and write the configured output paths.
- Nmap, arp-scan, ping, SNMP utilities, and optional Avahi mDNS tools.

```bash
sudo apt update
sudo apt install -y \
  python3 \
  nmap \
  arp-scan \
  iputils-ping \
  snmp \
  avahi-utils
```

No third-party Python package is required by the current script.

## Installation

```bash
git clone https://github.com/paulocfmarques-collab/network-discovery-prometheus.git
cd network-discovery-prometheus
python3 --version
chmod +x discovery.py
```

## Configuration

Create `config.json` beside `discovery.py`:

```json
{
  "linux": {
    "network": "192.168.1.0/24",
    "localhost": "192.168.1.10"
  }
}
```

| Key | Required | Description |
| --- | --- | --- |
| `network` | Yes | CIDR range passed to Nmap. |
| `localhost` | Yes | Local host IP used by the MAC lookup path. |

Optional lookup files:

- `mac_dictionary.json`: exact MAC address to friendly name.
- `vendors_dictionary.json`: vendor/OUI fallback lookup.
- `type_dictionary.json`: fallback device classification.

Example:

```json
{
  "00:11:22:33:44:55": "Core switch"
}
```

> The current implementation uses the SNMPv2c community string `public`. Treat this as a lab/default behavior and harden it before production use. Prefer SNMPv3 where possible.

## Running a discovery

```bash
sudo python3 discovery.py
```

The run loads state, discovers hosts, enriches them concurrently, writes monitoring artifacts, updates history, and restarts Telegraf.

Inspect the results:

```bash
tail -f discovery.log
python3 -m json.tool inventory.json
```

For unattended execution, use a systemd timer or cron after validating scan duration and network impact.

## Generated artifacts

| Artifact | Default path | Purpose |
| --- | --- | --- |
| Inventory | `inventory.json` | One enriched record per discovered host. |
| History | `history.json` | Persistent MAC-based identity and IP history. |
| Prometheus targets | `/etc/prometheus/targets/rede.json` | File-based discovery targets with labels. |
| Telegraf config | `/etc/telegraf/telegraf.d/ping.conf` | One `inputs.ping` block per host. |
| Logs | `discovery.log` | Rotating execution and error log. |

Inventory records include fields such as `ip`, `hostname`, `mac`, `vendor`, `device_type`, `previous_ips`, `latency_ms`, `os`, `open_ports`, `services`, and `snmp_sysdescr`.

Example Prometheus target:

```json
[
  {
    "targets": ["192.168.1.100"],
    "labels": {
      "friendly_name": "server01",
      "ip": "192.168.1.100",
      "mac": "00:11:22:33:44:55",
      "vendor": "Example Vendor",
      "device_type": "server",
      "os": "Linux",
      "latency_ms": "0.421",
      "network": "192.168.1.0/24"
    }
  }
]
```

## Prometheus integration

Configure Prometheus to watch the generated file:

```yaml
scrape_configs:
  - job_name: network-discovery
    file_sd_configs:
      - files:
          - /etc/prometheus/targets/rede.json
    relabel_configs:
      - source_labels: [__address__]
        target_label: instance
```

The same targets can be adapted for Blackbox Exporter or SNMP Exporter with exporter-specific relabeling. Ensure Prometheus can read the target file and that your deployment reloads file-SD changes as expected.

## Telegraf integration

The runner writes one ping input per inventory item:

```toml
[[inputs.ping]]
  urls = ["192.168.1.100"]
  count = 3
  timeout = 2.0

  [inputs.ping.tags]
    device = "server01"
```

It then invokes:

```bash
systemctl restart telegraf
```

Verify that the executing user can write the Telegraf configuration and restart the service.

## Security and operations

- Scan only authorized address ranges.
- Restrict permissions on inventory, history, and log files.
- Replace the hard-coded SNMP community with a configurable secret.
- Expect incomplete enrichment for firewalled, sleeping, virtual, or non-ARP devices.
- Nmap OS and service detection can be slow on larger networks.
- Validate generated JSON/TOML before restarting monitoring services.
- Consider atomic output writes for production deployments.

MAC addresses are the primary historical identity. NAT, virtual interfaces, MAC randomization, and hardware changes can cause devices to appear as new identities. Treat the inventory as discovery data, not as an authoritative CMDB.

## Troubleshooting

| Symptom | Checks |
| --- | --- |
| No hosts found | Validate `config.json`, CIDR notation, routing, firewall rules, and permissions. |
| Missing MAC/vendor | Confirm L2 adjacency and run `arp-scan --localnet`. |
| Empty OS/type | OS fingerprinting needs reachable ports and suitable privileges; use dictionary fallbacks. |
| Missing SNMP description | Confirm UDP/161 reachability, SNMP settings, and device ACLs. |
| Prometheus sees no targets | Check permissions, JSON validity, target path, and `promtool check config`. |
| Telegraf does not start | Inspect `journalctl -u telegraf`, validate generated TOML, and check permissions. |

```bash
sudo arp-scan --localnet
nmap -sn -R 192.168.1.0/24
python3 -m json.tool inventory.json
sudo promtool check config /etc/prometheus/prometheus.yml
sudo journalctl -u telegraf -n 100 --no-pager
```

## Roadmap

- [ ] Web dashboard and REST API
- [ ] NetBox integration
- [ ] PostgreSQL-backed inventory
- [ ] Containerized deployment
- [ ] Dedicated Prometheus exporter
- [ ] IPv6 discovery
- [ ] Configurable SNMP credentials and scan profiles
- [ ] Atomic output writes and configuration validation

## Contributing

1. Fork the repository.
2. Create a focused branch:

   ```bash
   git checkout -b feature/my-improvement
   ```

3. Test against an authorized lab network.
4. Keep documentation and operational requirements synchronized with the code.
5. Commit clearly and open a pull request with behavior, risks, and validation steps.

## License

This project is distributed under the MIT License. See [`LICENSE`](LICENSE) when available in the repository.

## Author

**Paulo Cesar Furlanetto Marques**  
[github.com/paulocfmarques-collab](https://github.com/paulocfmarques-collab)

<div align="center">

If this project helps keep network inventory and monitoring aligned, consider giving it a ⭐.

</div>
