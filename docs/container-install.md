# Containerized Matrix-LAN installer

This method packages the **installer** into a Docker image. The Matrix-LAN
services still run as separate containers managed by the generated Compose
project. It does **not** convert the bridges' SQLite databases to PostgreSQL.

## Prerequisites

Linux x86-64, Docker Engine, Docker Compose v2 and access to the Docker socket.
Run from a trusted host only: access to `/var/run/docker.sock` effectively
grants full administrative access to the host.

## Fresh installation

```bash
cd matrix-lan
bash docker-install.sh prepare --host matrix.home.arpa --bind 0.0.0.0 --port 18081 --owner owner
bash docker-install.sh start
bash docker-install.sh status
```

Use a hostname that resolves to the Docker host from client devices. The
installer image is built locally as `matrix-lan-installer:local`.

For a minimal installation:

```bash
bash docker-install.sh --runtime /absolute/path/to/matrix-lan-test prepare --core --host localhost --port 19081 --project matrix-lan-test
bash docker-install.sh --runtime /absolute/path/to/matrix-lan-test start
```

The `--runtime` directory must be **inside the repository directory** when
using this wrapper, since only that directory is mounted into the installer
container. To install outside it, extend the wrapper with an explicit matching
host/container bind mount; do not silently write to the container filesystem.

## Existing installation

```bash
bash docker-install.sh status
bash docker-install.sh password
bash docker-install.sh ca
bash docker-install.sh stop
```

The wrapper mounts the repository at the **same absolute host path** inside the
installer. This is required because the Docker daemon resolves bind mounts on
the host, not in the installer container.

Generated runtime state, PostgreSQL volumes and secrets stay on the host.
Back them up before upgrades. This workflow does not perform SQLite migration.

## Security

- Never expose the Docker socket over the network.
- Never run untrusted project code through the installer.
- Keep `runtime/` out of Git and back up `runtime/state/` plus PostgreSQL.
- No `docker compose down -v` is run by this installer.
