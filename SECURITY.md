# Security policy

Mas runs as root and manages nginx, systemd, certificates and DNS records, so security
reports are taken seriously.

## Reporting a vulnerability

Please **do not open a public issue**. Use GitHub's private reporting instead:
**Security → Report a vulnerability** on this repository.

Include what you can: affected version (`mas --version`), steps to reproduce, impact. You will
get an answer as soon as possible; fixes are released with a note in the changelog once users
have had a chance to update.

## Supported versions

Mas is in beta: only the latest release receives security fixes.

## Scope

In scope: the API and dashboard, authentication (passwords, TOTP, sessions, CSRF), the
deployment pipeline, the generated nginx/systemd configuration, the GitHub webhook, the DNS
provider integrations and the installer. The [security model](README.md#security-model)
describes what Mas is designed to protect against.
