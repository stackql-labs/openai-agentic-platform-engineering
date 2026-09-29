"""Edge autopilot: Cloudflare zone recon, a rate limit decision, and approval-gated tightening.

Built on the OpenAI Agents SDK with the StackQL MCP server (PyPI package stackql-mcp-server) as
the tool surface. The pattern follows stackql/edgepilot: a recon agent reads live zone analytics
and the rate limit ruleset, a decision step decides whether to tighten, and the change itself is a
single REPLACE statement rendered from a query file and executed by code after explicit approval.
"""
