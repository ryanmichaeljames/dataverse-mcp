# Security Policy

## Supported versions

Fixes land on the latest release only. Please reproduce on the current version of `dataverse-mcp` before reporting.

| Version | Supported |
|---------|-----------|
| 3.9.x   | Yes       |
| < 3.9   | No        |

## Reporting a vulnerability

Report privately through [GitHub security advisories](https://github.com/ryanmichaeljames/dataverse-mcp/security/advisories/new). Please do not open a public issue for a vulnerability.

Useful things to include:

- What an attacker can do, and what they need in order to do it
- The affected tool or module, and the version you reproduced on
- Steps to reproduce, with any real organization URL replaced by a placeholder

**Never include credentials, tokens, tenant or client IDs, or a real organization URL in a report.**

This project is maintained by one person in their own time, so there is no response-time guarantee. Expect an acknowledgement within a week or so, and a fix released once the issue is confirmed. Credit in the advisory and the changelog is offered unless you would rather stay anonymous.

## Scope

This is an MCP server that runs locally, alongside your MCP client, and talks to Microsoft Dataverse with your own credentials. Reports about the server's own behaviour are in scope — for example credential or token handling, the URL allowlist, injection through tool inputs, or server state leaking into tool responses.

Out of scope:

- Vulnerabilities in Microsoft Dataverse or the Power Platform. Report those to [MSRC](https://msrc.microsoft.com/report).
- Anything requiring an attacker who already controls the machine the server runs on, or its environment variables.
- Consequences of deliberately relaxing the safety flags — for example enabling `DATAVERSE_ALLOW_WRITE` or `DATAVERSE_ALLOW_DELETE`, or leaving `DATAVERSE_WHITELIST` unset on a shared deployment. Set `DATAVERSE_REQUIRE_WHITELIST=true` to fail closed when no allowlist is configured.
- Reports produced by a scanner with no demonstrated impact.

## Hardening

For any deployment that is not a single developer on their own machine:

- Set `DATAVERSE_WHITELIST` to the environments the server may reach, and `DATAVERSE_REQUIRE_WHITELIST=true` so a token is never minted for an unapproved host.
- Leave `DATAVERSE_ALLOW_WRITE` and `DATAVERSE_ALLOW_DELETE` unset unless write access is genuinely needed; they are off by default.
- Prefer the default `interactive` authentication, which is per-user and supports MFA.
