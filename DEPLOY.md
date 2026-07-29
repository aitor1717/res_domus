# Advanced: deploying to a server

This step is **optional**. The default way to use res_domus is local, with
`docker compose up` (see [README.md](README.md)). Local use needs no
server, domain, or account. Follow this guide only if you want your own
instance reachable from outside your home network, for example to use it
from your phone while away from home.

This guide sets up three things: a small VPS with a public IP, a free
DuckDNS subdomain, and Docker Compose running the app behind Caddy for
automatic HTTPS through Let's Encrypt. The steps work on any VPS. This
includes a free-tier instance (GCP's e2-micro, Oracle Cloud's Always Free
Ampere tier, and similar), a paid box, or a home server with port
forwarding. Each option works the same way once you have a public IP and
SSH access.

## 1. Get a free domain via DuckDNS

1. Go to https://www.duckdns.org and sign in with GitHub or Google.
2. Add a subdomain, for example `resdomus`. This gives you
   `resdomus.duckdns.org`.
3. Leave the IP field blank for now. You will fill it in once the VM has
   a public IP, in step 2.
4. Note your DuckDNS **token**, shown on the page. You need this token
   only if you want auto-updates. It is not required if the VM has a
   static IP.

## 2. Create a VM

Any small Linux VM works. In broad terms:

1. Start an Ubuntu or Debian instance with your provider. Request a
   **public static IP**.
2. Add your SSH public key during creation, or add it after through the
   provider's console or CLI.
3. Open inbound ports **80** and **443** (TCP) in the provider's firewall
   or security-group settings. Port 22 (SSH) is usually open by default.
   Some providers also block ports 80 and 443 at the OS `iptables` level,
   even after you open the cloud firewall. If `curl` from outside the VM
   times out after step 6, check this too.
4. Point the DuckDNS subdomain from step 1 at this VM's static IP.

## 3. Set up the VM

Connect over SSH (`ssh <user>@<your-static-ip>`), then run:

```bash
sudo apt update && sudo apt install -y docker.io docker-compose-v2
sudo usermod -aG docker $USER
newgrp docker
```

## 4. Transfer the project

This repository, or your fork of it, is the source of truth for code.
Real purchase data should never reach a public remote. Keep it local, and
copy it to the VM directly instead:

```bash
rsync -avz --exclude .venv --exclude __pycache__ --exclude .git --exclude data \
  /path/to/res_domus/ <user>@<your-static-ip>:~/res_domus/
```

This command excludes `data/` on purpose, even on the first copy. Run
`app/scripts/init_db.py` (or `seed_demo_data.py` for sample data) on the
VM itself in step 6 instead. If you sync `data/` from your machine, it
overwrites whatever the VM already has, without warning.

## 5. Configure secrets

On the VM, in `~/res_domus/`:

```bash
cp .env.example .env
nano .env   # fill in ANTHROPIC_API_KEY, SECRET_KEY, BASIC_AUTH_USER/PASS, DOMAIN
```

Set `DOMAIN` to your DuckDNS hostname, for example `resdomus.duckdns.org`.
Set `BASIC_AUTH_USER` and `BASIC_AUTH_PASS` too. These are **required**
once the app is reachable on the public internet. If you leave them
blank, the app disables auth entirely (see the README's Security and
privacy section). Caddy requests a Let's Encrypt certificate for `DOMAIN`
automatically on first start. This requires ports 80 and 443 to be
reachable, set up in step 2.

## 6. Run it

The Caddy reverse proxy handles HTTPS. It sits behind a Compose profile,
since local use does not need it. Enable this profile with
`--profile https`:

```bash
cd ~/res_domus
python3 app/scripts/init_db.py   # or seed_demo_data.py for sample data
docker compose --profile https up -d --build
```

Check the logs:

```bash
docker compose logs -f
```

Visit `https://<your-duckdns-domain>`. You should see a valid HTTPS
certificate and the Basic Auth prompt.

## 7. Updating later

```bash
# From your machine, after making changes locally. data/ is excluded every time.
rsync -avz --exclude .venv --exclude __pycache__ --exclude .git --exclude data \
  /path/to/res_domus/ <user>@<your-static-ip>:~/res_domus/

# On the VM:
cd ~/res_domus && docker compose up -d --build
```

The whole `data/` directory (the database, `input/`, `review/`,
`archive/`, `output/`) is bind-mounted. It persists across rebuilds on
its own. You never need to sync it in either direction as part of a
routine code update. If you need a local copy of the VM's database, for
backup or debugging, pull it explicitly:

```bash
rsync -avz <user>@<your-static-ip>:~/res_domus/data/res_domus.db ./data/res_domus.db
```
