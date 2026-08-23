# Atlas documentation

The root [README](../README.md) helps you decide whether Atlas is worth
evaluating and gets a fresh local deployment running. This documentation owns
the precise supported behavior: how Atlas makes decisions, where authority
lives, how it fails, which inputs operators control, and what the public
snapshot does and does not verify.

Use these layers as different kinds of evidence:

- **README:** product fit, current limits, preview, and shortest supported start.
- **Topic documentation:** exact architecture, configuration, operation,
  extension, and verification contracts.
- **Code, tests, audits, and smoke scripts:** executable evidence for the
  checked-out source tree. Passing them does not turn this technical-evaluation
  snapshot into a production release or operational certification.

## Start here

- [Project overview and quick start](../README.md)
- [Local Docker Compose deployment](deployment/local.md), including readiness,
  restart, recovery, and destructive reset behavior
- [Architecture: data lifecycle](architecture.md#data-lifecycle)

Atlas is currently a local, resettable technical-evaluation snapshot. It is not
Release Ready or Internet Ready, does not provide migration compatibility,
high availability, managed backup, monitoring, or a support SLA, and does not
include Provider credentials or deterministic expected answers.

## Understand

| Question | Exact owner |
|---|---|
| Where does each implementation component live? | [Architecture: source map](architecture.md#source-map) |
| What happens from sign-in to an answer? | [Architecture: user journey](architecture.md#user-journey) |
| Which component is authoritative for identity, content, execution, and Notes? | [Architecture: authority](architecture.md#authority) |
| How are access, evidence, lineage, and failures handled? | [Architecture: access, evidence, and failure behavior](architecture.md#access-evidence-and-failure-behavior) |
| What are the runtime semantics of directory identity? | [Architecture: identity directory integration](architecture.md#identity-directory-integration) |
| Which directory inputs can an operator configure? | [Configuration: LDAP and Active Directory connections](configuration.md#ldap-and-active-directory-connections) |
| What are the runtime semantics of Provider and model selection? | [Architecture: Provider and model routing](architecture.md#provider-and-model-routing) |
| Which Provider inputs and defaults can an operator configure? | [Configuration: Provider connections](configuration.md#provider-connections) |
| What do General and In-depth execute? | [Architecture: reasoning execution](architecture.md#reasoning-execution) |
| Which execution limits can an operator configure? | [Configuration: reasoning route policy](configuration.md#reasoning-route-policy) |
| Which member and administrator surfaces expose these capabilities? | [Architecture: product surfaces](architecture.md#product-surfaces) |
| What is retained, reset, or unsupported between snapshots? | [Architecture: data lifecycle](architecture.md#data-lifecycle) |

The UI names the two answer styles **General** and **In-depth**. Their exact
runtime values are `standard` and `deep`. System Admin names Prompt Skill
administration **Skill slots**; the architecture documentation retains the
precise Prompt Skills ownership and lifecycle terms.

## Operate

- [Configuration](configuration.md): bootstrap, Notes secrets, credential
  encryption, Provider and directory connections, reasoning policy, and
  runtime inputs
- [Local Docker Compose deployment](deployment/local.md): fresh evaluation,
  readiness, restart, recovery, replacement, and reset
- [Portainer with SMB](deployment/portainer-smb.md): audited deployment shape;
  real-environment operation remains unverified by the repository
- [Offline Portainer bundle](deployment/portainer-smb-offline.md): build,
  transfer, integrity, import, and audit steps

## Extend and verify

- [Processing plugin development](plugin-development.md)
- [Verification](verification.md): repository checks, publication-boundary
  audits, and conversation-evolution smoke
- [Development methodology](development-methodology.md): human authority,
  bounded agent work, and the public evidence boundary

## Participate

- [Contribution and community guide](../CONTRIBUTING.md): Issues, Discussions,
  documentation feedback, and deployment experiences; external pull requests
  are not currently accepted
- [Security policy](../SECURITY.md): private reporting direction; do not post
  vulnerabilities or sensitive material publicly
- [GitHub Issues](https://github.com/redstone39/atlas-public/issues) for specific,
  reproducible reports
- [GitHub Discussions](https://github.com/redstone39/atlas-public/discussions)
  for questions, experiences, architecture discussion, and broader ideas
