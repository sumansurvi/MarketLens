"""Provider adapters (Alpha Vantage, SEC EDGAR) and their fixture/live transports.

Adapters parse provider-shaped payloads into domain models and evidence. Transports
decide where payloads come from: the fixture transport reads labeled synthetic files;
live transports (Phase 2) call the providers with timeouts, rate limits and caching.
Both feed the same parsing code, so fixture tests exercise the live parsing paths.
"""
