# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [1.0.0] - 2026-04-09

### Added
- Initial release: run Claude Code on Amazon EC2 with a self-hosted open-source model via Ollama
- Automated EC2 setup script (`scripts/ec2-setup.sh`) for GPU instance provisioning
- SSH tunnel configuration for secure, port-free connectivity
- Sample project and test suite in `sample/`
- Support for Qwen 3.5-35B on NVIDIA L40S (g6e.xlarge)
- Configuration templates in `config/`
- Setup guide (`SETUP-GUIDE.md`) with step-by-step instructions
- Security findings documentation (`FINDINGS.md`)
