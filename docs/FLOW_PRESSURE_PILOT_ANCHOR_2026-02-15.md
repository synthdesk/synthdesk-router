# Flow Pressure Pilot Anchor (2026-02-15)

Captured UTC: 2026-02-15T01:24:40Z

## Included runtime package surface in this repo
- pyproject.toml
- scripts/flow_pressure_v0_runner.py
- packages/observer/flow/flow_pressure_v0.py
- router/utils/disk.py

## External dependency hashes (outside this repo)
- /root/synthdesk/utils/preflight_disk.py: 12b7b16a775c174435ede0e8c11c950536d3085e4f1cea43f3019520f55795df
- /etc/systemd/system/flow-pressure-v0.service: 0fcef937dc6dbc80581a0d52b80fec639c2092becac90c7ca8573b85565b417e
- /etc/systemd/system/flow-pressure-v0.service.d/disk-preflight.conf: 9d89a300a8e46f64fe38fd2f9900a52bfd5108f4c769ce4fe8241084833d407e

## Note
This commit anchors the pilot state for governance/custody where /root/synthdesk-router previously had no VCS metadata.
