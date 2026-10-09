# demo-day

Ansible-built demo stack for the TalTech IT demo day (9.12.2026), modelled on the
ICA0002 "IT Infrastructure Services" course: a web game whose scoreboard lives in
MySQL, backed up every minute, monitored with Prometheus + Grafana, fronted by HAProxy.

The point of the demo: the audience plays, scores land in the database, the database
gets dropped live, and `ansible-playbook restore.yaml` brings it back.

## Layout

| Role         | What it does                                                            |
|--------------|-------------------------------------------------------------------------|
| `mysql`      | MySQL 8 tuned for a 1 GB VM, `dino` database and user                   |
| `backup`     | `mysqldump` every minute via systemd timer, keeps the newest 120 dumps  |
| `haproxy`    | Host-based routing: `dino.*` → game, `grafana.*`, `prometheus.*`        |
| `dino`       | Flask + gunicorn: static dino runner, `/api/scores`, `/metrics`         |
| `prometheus` | Scrapes the game, node exporter and itself every 5 s                    |
| `grafana`    | Anonymous viewer access, provisioned Prometheus datasource + dashboard  |

## Usage

```bash
ansible-playbook infra.yaml              # build / converge everything
ansible-playbook infra.yaml --tags dino  # just the game
ansible-playbook restore.yaml            # restore newest dump
ansible-playbook restore.yaml -e dump=/var/backups/dino/dino-20261209T140000.sql
```

## Demo script

1. Open `http://dino.<domain>/` on the big screen, QR code for the audience. People play.
2. `http://grafana.<domain>/` shows rows in the DB climbing, backups ticking up every minute.
3. On the DB host: `sudo mysql -e 'DROP DATABASE dino'`. Scoreboard shows "Database not responding",
   Grafana's "Database reachable" goes red, HAProxy keeps the page itself up.
4. `ansible-playbook restore.yaml`. Scores are back, at most one minute lost (RPO 1 min).

## Hosts

`demo1` is an OCI free-tier VM that also runs an unrelated nginx site on 80/443, so HAProxy
listens on 8080 there (`host_vars/demo1.yaml`). Hostnames use nip.io, so no DNS setup:
`dino.129-151-208-139.nip.io:8080` etc. Set `demo_domain` in `group_vars/all.yaml` for a real domain.

## Load

`tests/load.py` simulates phones (keep-alive, leaderboard poll every 5 s, a score every ~20 s, page
loads spread over a QR-scan window). On the free-tier micro (1/8 OCPU, 1 GB) with 300 phones:
all 200s, leaderboard p95 94 ms, score post p95 402 ms. What made it work: HAProxy caches the
static files and the leaderboard JSON (1 s), gunicorn runs threaded, Grafana's heap is capped.
A same-second burst of 300 page loads without keep-alive still overloads the micro; real phones don't do that.
