# dino-game-web

The Chrome dinosaur game with a shared leaderboard, deployed by Ansible, built to be
**broken on stage and brought back with one command**.

The audience plays on their phones, scores land in a database, you drop the database in
front of everyone, run `ansible-playbook restore.yaml`, and the scores come back. The stack
is the one a university infrastructure course teaches: a database, minute-by-minute backups,
HAProxy in front, Prometheus and Grafana watching.

![stack](https://img.shields.io/badge/ansible-%E2%89%A52.14-black) ![license](https://img.shields.io/badge/license-MIT-green)

## What you get

| Role         | What it does                                                                     |
|--------------|----------------------------------------------------------------------------------|
| `database`   | MySQL, PostgreSQL or SQLite (`db_engine`), tuned to fit a 1 GB VM                |
| `backup`     | Dump every minute via a systemd timer, keep the newest N. MySQL and SQLite today |
| `haproxy`    | Host-based routing, caches the game files and the leaderboard JSON               |
| `dino`       | The game (Chromium's T-Rex runner) + a small Flask API, run by gunicorn          |
| `prometheus` | Scrapes the game, node exporter and itself                                       |
| `grafana`    | Anonymous viewer access, provisioned dashboard: DB reachable, rows, top score    |

Pages: `/` the game (asks for a name, auto-starts, touch buttons for jump and duck),
`/board` a big-font leaderboard for the projector, `/metrics` for Prometheus.

## Quick start

```bash
git clone https://github.com/KongFuzi1/dino-game-web && cd dino-game-web
ansible-galaxy collection install community.mysql community.postgresql
cp hosts.example hosts            # put your Ubuntu 22.04/24.04 box in it
vim group_vars/all.yaml           # demo_domain, db_engine, password, game settings
ansible-playbook infra.yaml
```

Then open `http://dino.<your demo_domain>/`. With `demo_domain: 203-0-113-10.nip.io` style
hostnames there is no DNS to set up: nip.io resolves to the IP inside the name.

Smallest possible run: `db_engine: sqlite`, `monitoring_enabled: false`. That is the game,
a leaderboard, backups and HAProxy on any box with 256 MB to spare.

## The demo

1. Put the QR code for `http://dino.<domain>/` on the screen. People play.
2. Show `/board` and Grafana: rows in the database climbing, backups ticking up every minute.
3. Kill it, on the DB host:
   - MySQL: `sudo mysql -e 'DROP DATABASE dino'`
   - SQLite: `sudo rm /var/lib/dino/scores.db`
   The game page keeps loading (HAProxy, static files cached) but says "Database not responding".
   Grafana's "Database reachable" goes red. `/board` goes red.
4. `ansible-playbook restore.yaml`. The newest usable dump is imported and the playbook prints
   how many scores are back. At most one minute of play is lost: that's the RPO, say so out loud.

A dump taken while the database was gone is empty; the restore skips those automatically.
`-e dump=/var/backups/dino/dino-20261209T140000.sql` restores a specific one.

## Settings (`group_vars/all.yaml`)

| Variable                                 | Default           | Notes                                                 |
|------------------------------------------|-------------------|-------------------------------------------------------|
| `demo_domain`, `haproxy_port`            | nip.io, 80        | hostnames are `dino.`, `grafana.`, `prometheus.` + domain |
| `dino_source`, `dino_repo`, `dino_version` | github, this repo, v1.0.0 | `github` fetches `app/` from a tag/branch/commit of the repo |
| `game_title`, `game_acceleration`, `game_max_speed` | DINO, 0.0025, 20 | Chrome's own values are 0.001 and 13, much longer games |
| `leaderboard_size`, `poll_interval_ms`, `cache_ttl` | 10, 5000, 1 | the leaderboard JSON is cached in the app and in HAProxy |
| `db_engine`, `db_name`, `db_user`, `db_password` | mysql, dino, dino, env `DINO_DB_PASSWORD` or a placeholder | **change the password** |
| `sqlite_path`                            | /var/lib/dino/scores.db |                                                 |
| `backup_enabled`, `backup_dir`, `backup_keep`, `backup_interval` | true, /var/backups/dino, 30, every minute | postgres: set `backup_enabled: false` for now |
| `monitoring_enabled`, `grafana_admin_password` | true, = db_password | Prometheus + Grafana need ~250 MB RAM           |

Per-host overrides go in `host_vars/<name>.yaml` (gitignored), e.g. `haproxy_port: 8080` when
something else already owns port 80 on that box.

## Load

`tests/load.py` simulates phones: keep-alive, a leaderboard poll every 5 s, a score every
~20 s, page loads spread over a QR-scan window.

```bash
python3 tests/load.py http://dino.<domain>:80 300 90 30
```

On an Oracle free-tier micro (1/8 OCPU, 1 GB, MySQL + Grafana + Prometheus on the same box)
300 simulated phones gave all 200s, leaderboard p95 94 ms, score post p95 402 ms.

`tests/play-round.mjs` drives a full round (name, start, jump, duck, crash, save, restart)
through Chrome DevTools: `node tests/play-round.mjs http://dino.<domain>/` (node >= 22, Chrome installed).

## Layout

```
app/            the game and API (what the dino role deploys, locally or from GitHub)
roles/          database, backup, haproxy, dino, prometheus, grafana
infra.yaml      build everything          restore.yaml   bring the database back
group_vars/     settings                  hosts.example  inventory template
tests/          load simulator, browser round
```

## Credits and license

MIT for everything here except `app/static/runner.js`, `runner.css` and `assets/`, which are
the Chromium T-Rex runner, © The Chromium Authors, BSD 3-Clause
(`app/static/LICENSE.chromium-runner`).

Built for the TalTech IT demo day, December 2026, to show what the ICA0002 "IT Infrastructure
Services" course makes you build.
