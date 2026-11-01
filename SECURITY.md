# Security Policy

Please do not report security vulnerabilities in a public issue, pull request,
or discussion.

This project parses untrusted files, so crashes or memory errors on malformed
input and reads outside an asset's permitted location are in scope. The
[external resource policy](docs/architecture/IMPORT_PIPELINE.md#external-resource-policy)
describes the intended URI restrictions and their limits.

## Report privately

Use GitHub's private vulnerability reporting for this repository:

<https://github.com/animu-sphere/usd-gltf-plugins/security/advisories/new>

Include enough detail to reproduce the problem, the affected commit, and any
suggested mitigation. Do not include secrets or private user data in the report.

If the form is unavailable, open a public issue asking for a private contact,
without any details of the vulnerability.

Maintainers will acknowledge the report when they can, investigate it, and
coordinate disclosure and a fix with the reporter. Please give maintainers a
reasonable opportunity to address a vulnerability before making it public.

The `main` branch is the supported security target. After the first release,
the latest release is also supported.
