"""MCP: a second way in to the same Admin OS, not a second Admin OS.

ChatGPT can hold one MCP connector and the Gmail app in the same conversation,
which is the only reason this transport exists: the review needs Gmail read by
the client and the process owned by the service, in one place.

Everything here is a thin adapter. The tools parse their arguments, call an
application service, and render what it returns. No rule, no validation and no
lifecycle decision lives in this package — a second copy of the process is a
second process, and it would be the one nobody tested.
"""
