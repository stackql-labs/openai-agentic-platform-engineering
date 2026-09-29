"""Edge autopilot: Cloudflare zone recon, a rate limit decision, and approval-gated tightening.

Built on the OpenAI Agents SDK with the StackQL MCP server (PyPI package stackql-mcp-server) as
the tool surface. The pattern follows stackql/edgepilot: a recon agent, working from an intent
prompt, discovers the zone analytics and the rate limit ruleset through the query library and the
discovery tools; a decision agent applies the policy and discovers the write contract for the
ruleset phase, proposing the exact statement; code checks that statement against an allowlist and
executes it, once, after explicit approval.
"""
