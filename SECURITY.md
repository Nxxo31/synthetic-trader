# Security Policy

## Supported Versions

The following versions are currently being supported with security updates:

| Version | Supported          |
| ------- | ------------------ |
| latest  | :white_check_mark: |
| < 1.0   | :x:                |

## Reporting a Vulnerability

If you discover a security vulnerability within this project, please report it
responsibly by emailing **sebastian.velasco@example.com** instead of using a
public issue tracker.

Please include the following information in your report:

- A description of the vulnerability and its potential impact
- Steps to reproduce the issue
- Any relevant logs, screenshots, or proof-of-concept code
- Your assessment of severity (low, medium, high, critical)

### Response Timeline

- **Acknowledgment**: Within 48 hours
- **Initial Assessment**: Within 5 business days
- **Resolution or Mitigation**: Depends on severity, typically 7-30 days

### Expectations

- Please do not publicly disclose the vulnerability until it has been fixed.
- We will credit reporters (with permission) in release notes or a security
  advisory.
- We may ask for additional information to reproduce or validate the issue.

## Security Best Practices

When contributing to this project:

- Never commit secrets, API keys, or credentials to the repository.
- Use environment variables or a secrets manager for sensitive configuration.
- Review dependencies regularly for known vulnerabilities.
- Follow the principle of least privilege when designing access controls.
