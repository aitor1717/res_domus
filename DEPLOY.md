# Advanced: deploying to a server (Oracle Cloud Always Free)

This is **optional**. The default way to use res_domus is locally via
`docker compose up` (see [README.md](README.md)) — no server, domain, or
account needed. Only follow this guide if you want your own instance
reachable from outside your home network (e.g. to use it from your phone
away from home).

This sets up: an Always Free Ampere A1 VM running Ubuntu, a free DuckDNS
subdomain, and Docker Compose running the app behind Caddy (automatic
HTTPS via Let's Encrypt). The same approach works on any VPS with a public
IP — Oracle's Always Free tier is just one $0 option (others: GCP e2-micro,
a home server with port forwarding, etc.).

## 1. Get a free domain via DuckDNS

1. Go to https://www.duckdns.org and sign in (GitHub/Google login).
2. Add a subdomain, e.g. `resdomus` → you get `resdomus.duckdns.org`.
3. Note the IP field — leave it blank for now, you'll fill it in once the
   VM has a public IP (step 3).
4. Note your DuckDNS **token** (shown on the page) — you'll need it later
   if you want auto-updates (not required if the VM has a static IP, see
   step 2).

## 2. Create the Always Free VM

1. Sign up / log in at https://cloud.oracle.com (requires a credit card
   for verification, but Always Free resources are never charged).
2. Go to **Compute → Instances → Create Instance**.
3. Image: **Ubuntu 22.04** (or latest LTS). Shape: click "Change shape" →
   **Ampere → VM.Standard.A1.Flex** → 4 OCPU / 24GB RAM (max Always Free
   allowance — you can use less).
4. Under "Add SSH keys", upload your public key (`~/.ssh/id_ed25519.pub`)
   or generate a new pair and download the private key.
5. Create the instance. Note its **public IP** once it's running.
6. Go to **Networking → Virtual Cloud Networks → (your VCN) → Security
   Lists → Default Security List → Add Ingress Rules**:
   - Source CIDR `0.0.0.0/0`, IP Protocol TCP, Destination Port `80`
   - Source CIDR `0.0.0.0/0`, IP Protocol TCP, Destination Port `443`
   - (port 22/SSH is open by default)
7. **Reserve the IP as static** (Always Free includes 1 reserved public
   IP): Networking → IP Management → Reserved Public IPs → create one,
   then attach it to the instance's VNIC, replacing the ephemeral IP.
8. Back on DuckDNS, set that static IP as the subdomain's IP address.

## 3. Set up the VM

SSH in (`ssh ubuntu@<your-static-ip>`), then:

```bash
sudo apt update && sudo apt install -y docker.io docker-compose-v2
sudo usermod -aG docker $USER
newgrp docker
```

Oracle's default `iptables` rules also block 80/443 at the OS level —
open them:

```bash
sudo iptables -I INPUT -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save   # if installed; else add to /etc/iptables/rules.v4
```

## 4. Transfer the project

This repo contains your real grocery data and is **not** pushed to a
public remote. Copy it to the VM directly, e.g. from your machine:

```bash
rsync -avz --exclude .venv --exclude __pycache__ --exclude .git \
  /home/aitor1717/Documents/Code/res_domus/ ubuntu@<your-static-ip>:~/res_domus/
```

## 5. Configure secrets

On the VM, in `~/res_domus/`:

```bash
cp .env.example .env
nano .env   # fill in ANTHROPIC_API_KEY, SECRET_KEY, BASIC_AUTH_USER/PASS, DOMAIN
```

`DOMAIN` should be your DuckDNS hostname, e.g. `resdomus.duckdns.org`.
Caddy will automatically request a Let's Encrypt certificate for it on
first start (requires ports 80/443 reachable, set up in step 2).

## 6. Run it

The Caddy reverse proxy (for HTTPS) is behind a Compose profile since it's
not needed for local use — enable it here with `--profile https`:

```bash
cd ~/res_domus
docker compose --profile https up -d --build
```

Check logs:

```bash
docker compose logs -f
```

Visit `https://resdomus.duckdns.org` — you should get a valid HTTPS
cert and the Basic Auth prompt.

## 7. Updating later

```bash
# from your machine, after making changes locally
rsync -avz --exclude .venv --exclude __pycache__ --exclude .git \
  /home/aitor1717/Documents/Code/res_domus/ ubuntu@<your-static-ip>:~/res_domus/

# on the VM
cd ~/res_domus && docker compose up -d --build
```

The whole `data/` directory (DB, `input/`, `review/`, `archive/`, `output/`)
is bind-mounted, so it persists across rebuilds — but **be careful with
rsync direction**: syncing from your machine to the VM will overwrite the
VM's `data/res_domus.db` with your local copy. Once the VM is the live
instance, treat the VM's DB as the source of truth and pull it back before
syncing code changes:

```bash
rsync -avz ubuntu@<your-static-ip>:~/res_domus/data/res_domus.db ./data/res_domus.db
```
