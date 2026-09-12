G-FLO test suites
=================
Three suites, 304 checks. All three pass on this build.

  pentest_deep.py           204 checks — the penetration test
  security_regression.py     39 checks — the 18 original security findings
  functional_regression.py   61 checks — the shop and admin actually working

HOW TO RUN
----------
Each suite talks to a running server over HTTP, so start one first. They
expect different ports and databases (so a suite that dirties data cannot
affect another):

  pentest_deep.py            127.0.0.1:8500   DB /tmp/ptdata/gflo.db
  security_regression.py     127.0.0.1:8361   DB set at the top of the file
  functional_regression.py   127.0.0.1:8362   DB set at the top of the file

The constants are at the top of each file — B for the base URL, DB for the
database path, and the admin password. Point them at a scratch copy of the
database, never at production: these suites deliberately create, edit and
delete records, upload junk files, and try to break things.

For the pen test suite, the server must be started with the host-split
variables set, because several checks exercise host routing:

  DATA_DIR=/tmp/ptdata SITE_DIR=/path/to/site \
  SECRET_KEY=pentestsecret0123456789abcdef \
  ADMIN_HOST=admin.gflo.test SITE_HOST=gflo.test STORE_HOST=store.gflo.test \
  uvicorn app.main:app --host 127.0.0.1 --port 8500 --no-proxy-headers

Then:  python tests/pentest_deep.py


TWO THINGS THAT WILL CONFUSE YOU IF NOBODY SAYS THEM
----------------------------------------------------
1. The brute-force section is LAST on purpose. It works, which means it locks
   the testing IP out for 15 minutes. If it ran earlier, everything after it
   would fail with what look like authentication bugs. For the same reason,
   RESTART THE SERVER before re-running the suite — the lockout counter lives
   in process memory, so a restart is what clears it.

2. functional_regression.py duplicates a product as one of its checks and
   asserts exactly one copy exists. Run it twice against the same database and
   the second run fails on a stale row, not a real bug. Clear it with:

     delete from products where name like 'WorkflowProd%';


WHAT THE PEN TEST COVERS
------------------------
  path traversal and arbitrary file read      11 payloads
  source and config disclosure                11 probes
  session forgery, expiry and revocation      forged, unsigned, unknown user,
                                              stale token_version, 400 days old
  access control                              every admin GET and POST, signed out
  CSRF                                        every state-changing endpoint
  SQL injection                                8 payloads
  template injection, CRLF, command injection
  XSS                                         stored, reflected, JSON contexts
  file upload abuse                           PHP, SVG, HTML, polyglot,
                                              traversal filename, 81 MP bomb
  business logic                              infinity, NaN, 1e308, negatives
  host header and open redirect                9 redirect variants
  denial of service                           ReDoS, oversized bodies, pagination
  security headers, cookie flags, config exposure
  error verbosity
  privilege boundaries                        owner / admin / editor
  CSV export and import safety
  regression cover for the 5 bugs this pass found


A NOTE ON READING THE OUTPUT
----------------------------
A failing check is a claim, not a conclusion. During this pass four checks
reported problems that turned out to be faults in the tests themselves — an
escaped payload that a substring match mistook for live markup, a rate limiter
that returns HTTP 200 with a message rather than 429, a login that had already
been locked out, and a 422 that only meant "no file attached". Each one was
verified by hand before being believed. Do the same: reproduce a failure
manually before changing any code.
