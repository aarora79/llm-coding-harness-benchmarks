# Security Policy

## Reporting a Vulnerability

We take the security of this project seriously. If you believe you have found a security vulnerability, please report it to us privately. **Do not report security vulnerabilities through public GitHub issues, discussions, or pull requests.**

Instead, please use one of the following channels:

- **GitHub Security Advisories (preferred):** open a private report through the repository's [Security tab](../../security/advisories/new). This keeps the details confidential until a fix is available.

Please include as much of the following as you can, to help us triage quickly:

- The type of issue (e.g. injection, secret exposure, SSRF, privilege escalation).
- The affected file(s), configuration, or component.
- Step-by-step instructions to reproduce the issue.
- Proof-of-concept or exploit code, if available.
- The impact, including how an attacker might exploit it.

## What to Expect

- **Acknowledgement:** we aim to acknowledge your report within 3 business days.
- **Updates:** we will keep you informed of our progress as we investigate and work on a fix.
- **Disclosure:** we follow coordinated disclosure. We ask that you give us a reasonable window to release a fix before any public disclosure, and we will credit you for the report unless you prefer to remain anonymous.

## Supported Versions

This project is a reference/benchmarking harness rather than a versioned product; security fixes are applied to the `main` branch. Please make sure you are running the latest `main` before reporting.

## Scope

Reports about the code in this repository are in scope. Vulnerabilities in third-party dependencies should be reported to the respective upstream projects, though we welcome a heads-up so we can update our dependency floors.
