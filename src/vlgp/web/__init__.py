"""vlgp.web — T15 Web platform backend (FastAPI).

PM architecture decision (AGENT_HANDOFF T15, 2026-08-23): FastAPI backend
reusing the vlgp package (session layout / metadata / annotations read-write
+ JPEG preview frame service) with a static JS frontend (canvas frame-index
rendering + prefetch sliding window; no <video> seek).

v0: platform skeleton + T10 trim functionality migration (session browse,
three synchronised frame views, IN/OUT trim, approved/rejected status,
save -> annotations.json, byte-identical with the PyQt5 tool).
"""
