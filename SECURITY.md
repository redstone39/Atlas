# Security policy

## Supported scope

This repository is a technical-evaluation snapshot with a
`resettable_development` lifecycle. It is not Internet Ready or Release Ready.
Security reports are welcome for the current default branch, but no production
support SLA is offered.

## Reporting

Do not open a public issue containing credentials, private documents,
vulnerability details, or an exploit. Contact the repository owner privately
before public disclosure. Include:

- the affected commit and component;
- the supported path used to reproduce the issue;
- expected and observed behavior;
- the minimum safe reproduction without private data.

Never submit live Provider keys, SMB credentials, database dumps, uploaded
documents, access tokens, or unredacted logs.

## Deployment boundary

The default Compose deployment binds public-facing ports to loopback. Public
Internet exposure, TLS, firewalling, monitoring, backups, capacity, host
hardening, and abuse controls require a separate operator design.
