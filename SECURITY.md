# Security Policy

## Supported versions

FrameDrop is a single, actively developed version — there are no maintained
older branches. Always run the latest `main`.

## Reporting a vulnerability

Please do **not** open a public GitHub issue for security vulnerabilities.

Instead, report it privately via
[GitHub Security Advisories](https://github.com/saurowankhade/FrameDrop/security/advisories/new)
for this repository. Include:

- A description of the vulnerability and its impact
- Steps to reproduce (a sample `.screenstudio`/zip if relevant)
- Any suggested fix, if you have one

We'll acknowledge reports as soon as possible and aim to ship a fix promptly
for confirmed issues.

## Notes for self-hosters

FrameDrop accepts file uploads and shells out to `ffmpeg`. If you deploy it
publicly:

- Set `FRAMEDROP_MAX_UPLOAD` and put a reverse proxy / size limit in front.
- Run it as an unprivileged user/container — the provided `Dockerfile` does
  not need root.
- Uploaded archives and intermediates are deleted after each job; rendered
  MP4s are swept after one hour. Don't disable this in a fork without adding
  your own cleanup.
