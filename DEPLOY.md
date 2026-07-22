# Advanced: deploying to a server

This is **optional**. The default way to use res_domus is locally via
`docker compose up` (see [README.md](README.md)) — no server, domain, or
account needed. Only follow this guide if you want your own instance
reachable from outside your home network (e.g. to use it from your phone
away from home).

This sets up: a small VPS with a public IP, a free DuckDNS subdomain, and
Docker Compose running the app behind Caddy (automatic HTTPS via Let's
Encrypt). This works on any VPS — a free-tier instance (Oracle Cloud's
Always Free Ampere tier, GCP's e2-micro, etc.), a paid box, or a home
server with port forwarding all work the same way once you have a public
IP and SSH access.

## 1. Get a free domain via DuckDNS

1. Go to https://www.duckdns.org and sign in (GitHub/Google login).
2. Add a subdomain, e.g. `resdomus` → you get `resdomus.duckdns.org`.
3. Note the IP field — leave it blank for now, you'll fill it in once the
   VM has a public IP (step 2).
4. Note your DuckDNS **token** (shown on the page) — you'll need it later
   if you want auto-updates (not required if the VM has a static IP).

## 2. Create a VM

Any small Linux VM works. In broad strokes:

1. Spin up a Ubuntu/Debian instance on whichever provider you're using,
   with a **public static IP**.
2. Add your SSH public key during creation (or after, via the provider's
   console/CLI).
3. Open inbound ports **80** and **443** (TCP) in the provider's
   firewall/security-group settings — port 22/SSH is usually open by
   default. Some providers also block 80/443 at the OS `iptables` level
   even after the cloud firewall is open; if `curl` from outside times out
   after step 6, check that too.
4. Point the DuckDNS subdomain from step 1 at this VM's static IP.

## 3. Set up the VM

SSH in (`ssh <user>@<your-static-ip>`), then:

```bash
sudo apt update && sudo apt install -y docker.io docker-compose-v2
sudo usermod -aG docker $USER
newgrp docker
```

## 4. Transfer the project

This repo (or your fork of it) is the source of truth for code, but real
purchase data should never be pushed to a public remote — it stays local
and gets copied to the VM directly instead:

```bash
rsync -avz --exclude .venv --exclude __pycache__ --exclude .git --exclude data \
  /path/to/res_domus/ <user>@<your-static-ip>:~/res_domus/
```

`data/` is excluded deliberately even on the *first* copy — run
`app/scripts/init_db.py` (or `seed_demo_data.py` for sample data) on the
VM itself in step 6 instead of syncing a local copy over. Syncing `data/`
from your machine will silently overwrite whatever the VM already has.

## 5. Configure secrets

On the VM, in `~/res_domus/`:

```bash
cp .env.example .env
nano .env   # fill in ANTHROPIC_API_KEY, SECRET_KEY, BASIC_AUTH_USER/PASS, DOMAIN
```

`DOMAIN` should be your DuckDNS hostname, e.g. `resdomus.duckdns.org`.
`BASIC_AUTH_USER`/`BASIC_AUTH_PASS` are **required** once this is reachable
on the public internet — leaving them blank disables auth entirely (see
README's Security & privacy section). Caddy will automatically request a
Let's Encrypt certificate for `DOMAIN` on first start (requires ports
80/443 reachable, set up in step 2).

## 6. Run it

The Caddy reverse proxy (for HTTPS) is behind a Compose profile since it's
not needed for local use — enable it here with `--profile https`:

```bash
cd ~/res_domus
python3 app/scripts/init_db.py   # or seed_demo_data.py for sample data
docker compose --profile https up -d --build
```

Check logs:

```bash
docker compose logs -f
```

Visit `https://<your-duckdns-domain>` — you should get a valid HTTPS cert
and the Basic Auth prompt.

## 7. Updating later

```bash
# from your machine, after making changes locally — data/ is excluded every time
rsync -avz --exclude .venv --exclude __pycache__ --exclude .git --exclude data \
  /path/to/res_domus/ <user>@<your-static-ip>:~/res_domus/

# on the VM
cd ~/res_domus && docker compose up -d --build
```

The whole `data/` directory (DB, `input/`, `review/`, `archive/`, `output/`)
is bind-mounted, so it persists across rebuilds on its own — there's no
reason to ever sync it in either direction as part of a routine code
update. If you genuinely need a local copy of the VM's database (e.g. for
backup or debugging), pull it explicitly and deliberately:

```bash
rsync -avz <user>@<your-static-ip>:~/res_domus/data/res_domus.db ./data/res_domus.db
```
