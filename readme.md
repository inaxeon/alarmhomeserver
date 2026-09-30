# Alarm Home Server - Reverse engineered server for legacy / orphaned Climax (Yale and other) alarm systems

This repository contains a reverse engineered server for some, typically older Climax alarm systems. [Climax Technology](https://www.climax.com.tw/) is a white label alarm system manufacturer whose product is sold under many different brands throughout the world. Some of its currently known larger customers are Yale (UK), Abode (USA) and Blaupunkt (Germany). The full list is probably enormous. Climax (sometimes) sells the systems under their own VESTA brand.

The internet enabled versions of these systems are typically controlled through a smartphone app, which connects to a proprietary cloud service. Each re-seller has its own app which very rarely exposes all of the features of the underlying system. Quality of user experience depends on how much the re-seller is able to spend on the app thus systems requiring a monthly fee generally have better apps.
# What is this project?

The project began (in private) more than a decade ago in early 2016. Frustrated by the poor user experience and lack of reliable home automation integration options of Yale alarm systems, a better solution was wanted. Most would say dispose of the system but it is a less straightforward proposition when significant investment has already been made.

Early reverse engineering efforts on this project discovered that poor user experience and reliability was entirely attributed to the the cloud service and app. The physical equipment inside the home is well engineered and practically bug-free.

Some prior integration efforts exist which typically try to hook up to the re-seller's official cloud API (by reverse engineering it). Perfectly fine if it's reliable and the re-seller is still in business. Other integrations call into the web UI on the hub (if they can get access to it) - a better approach but it's an ugly way to interface with these systems.

In this project the official (typically XMPP based) management interface is exhaustively reverse-engineered and an entirely new open-source stateful server with REST API, web interface and email notification service is provided. It is designed to completely replace the original cloud service and smartphone app these systems are typically managed with. With this project the entire system can be operated completely offline, within your home, under your control. No bugs. No spinners. No crappy app which can't remember your credentials for more than two days.

OK so there might be bugs. But we can fix them :)
# Which alarm systems will it work with?

Presently, not many. See the [hardware page for more details](docs/hardware.md).
# Configuring a system to work with this project

First read the [hardware page](docs/hardware.md) to see if you have a compatible system.

* [MZ-1 configuration](docs/configure_mz1.md)
* [CTC-1815 configuration](docs/configure_1815.md)
* [CTC-1735 configuration](docs/configure_1735.md)
# Web interface

For an indication of the feature set some screenshots of the web UI can be seen [here](docs/webui.md).
# Protocol types

Climax alarms communicate with servers using several different protocols which are [detailed here](docs/protocols.md).
# AI Disclosure

With the original project having been developed over a decade ago the codebase had become old and stale. To prepare for public release AI was used as following:

* To modernise the human-written backend to bring it up to date with modern Python, re-do the API using FastAPI, re-do the config using TOML etc. The changes made were carefully reviewed to ensure it remained functionally and structurally equivalent to the original codebase, which contains many intricacies, which many human hours had been spent perfecting. All of the supported hardware has been re-tested on the modernised codebase.
* The original human-written Angular 1 web interface was scrapped in favour of a new pure javascript UI generated entirely by AI. Not proud of it but the new interface is quite a lot nicer than the old one. The web UI code has not had much human review but has been tested to function as expected. It was built in less than two hours.

## Requirements

- Python 3.11+
## Installation

```bash
git clone <this repo>
cd alarmhomeserver
python -m venv .venv

# Linux/macOS
source .venv/bin/activate

# Windows
.venv\Scripts\activate

pip install -r requirements.txt

```

## Configuration

An example configuration file is provided: `config.toml`
## Running manually (foreground)
  
```bash
python -m alarmhomeserver.main config.toml
```
## Running as a daemon/service (systemd)

Create `/etc/systemd/system/alarmhomeserver.service`:
 
```ini
[Unit]
Description=Alarm Home Server
After=network.target

[Service]
Type=simple
User=alarmhomeserver
WorkingDirectory=/opt/alarmhomeserver
ExecStart=/opt/alarmhomeserver/.venv/bin/python -m alarmhomeserver.main config.toml
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

  Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now alarmhomeserver
sudo systemctl status alarmhomeserver

journalctl -u alarmhomeserver -f   # follow logs
```

Note: binding to ports below 1024 (e.g. the HTTP polling port, 80) requires either running as root or granting explicit permission.
## License

GNU General Public License v2.0 or later - see individual source file headers.