# Electricity Plan Comparison Q&A Agent

AI agent for comparing electricity plans and answering smart-contract questions, built with Agentic Star.

> **Category**: Cat 2 (domain-specific retrieval-augmented pipeline)
> **Industry**: Energy
> **Template ID**: ENE-C2-014

## Overview

Answers questions about Japanese retail electricity plans. Given a natural-language
question, it retrieves from the governing statute, registered plan terms, and government
incentive-programme rules, then returns a cited plan comparison, an eligibility
determination for the applicable incentive programmes, and enrollment guidance.

Callers may supply contract parameters — supplier, customer segment, contract capacity,
monthly consumption, specific plan codes — and those change the answer: a contract
capacity below the high-voltage threshold rules high-voltage plans out and says so.

The agent never produces an individualized billing or rate projection. That is the
boundary it is built around rather than a missing feature: consumption is reported as a
band, no cost is computed anywhere in the pipeline, and an output gate independently
refuses any answer that crosses the line.

This is an agent template built with the **AGENTIC STAR** development platform and the
**AgentCore Framework**. It is intended to be taken as a starting point: fork it, adapt it to
your own data and policies, and run it inside your own AGENTIC STAR deployment.

## Requirements

**This template does not run standalone.** It requires:

| Requirement | Notes |
|---|---|
| **AGENTIC STAR platform** | The agent connects to the platform at start-up. Without it, start-up fails immediately (see *Behaviour without the platform* below). Deployment guides and API documentation: [AGENTIC STAR Developers](https://developers.fd.agenticstar.tm.softbank.jp/) |
| **AgentCore Framework** (`agenticstar-agentcore`) | Installed from PyPI as a dependency. |
| Python | >=3.11 |

```bash
pip install -e .
```

### Behaviour without the platform

The framework is designed to run **only** on AGENTIC STAR. There is no fallback or degraded
mode: if the platform is unreachable or the installed SDK does not match, start-up fails rather
than bringing up a partially working agent. This is intentional — a half-running agent is worse
than one that refuses to start.

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

Tests run without a platform connection. Running the agent itself does not.

## Project Structure

```
src/          agent implementation (nodes, services, schemas)
tests/        unit, integration and boundary tests
config/       agent configuration
docs/         design and operational documentation
```

See `docs/` for the design and the test specification.

## Customising

1. Adjust `config/` for your own environment and policies.
2. Replace the knowledge sources and sample data with your own.
3. Review the node implementations under `src/nodes/` for domain-specific logic.
4. Re-run the test suite.

## License

MIT — see [LICENSE](LICENSE).

## Status of this repository

This template is published **as is**, by its individual author, under the MIT license. It carries
**no warranty and no support commitment**, and no organisation stands behind its behaviour or
fitness for any purpose. Issues and pull requests may or may not receive a response; that is at
the sole discretion of the repository owner.

---

