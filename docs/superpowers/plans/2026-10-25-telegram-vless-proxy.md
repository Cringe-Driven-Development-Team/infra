# Telegram egress via VLESS on auxiliary VPS

Part of [webhook alerts Task 10](https://github.com/Cringe-Driven-Development-Team/.github/issues/20) and [infra#76](https://github.com/Cringe-Driven-Development-Team/infra/issues/76).

## Decision and constraints

The auxiliary VPS times out on direct Telegram Bot API calls. A VLESS over Reality server supplied by the owner is reachable by TCP from the VPS. Use official Xray Core as a sidecar in the existing `/opt/hooks` Compose project. Its HTTP proxy listens only on an unexposed Docker network shared with `hooks`; Caddy does not join that network. Route only `api.telegram.org` through VLESS and send unmatched destinations to a blackhole outbound. Keep `WEBHOOK_ALERTS_SEND_ENABLED=false` until real delivery and queue cutover checks pass. The webhook service's `TELEGRAM_PROXY_URL=http://telegram-proxy:2080` is consumed by the `.github` image published from merge `4a4064d`.

The VLESS URI is secret. Convert the currently active second URI locally into `vault_hooks_proxy_config` (structured Xray JSON object) in encrypted `ansible/inventory/group_vars/all/vault.yml`. The owner may later replace it with the first URI after separate testing. Do not add plaintext URI, UUID, Reality public key, server address or bot token to Git, logs, tests or review output. Existing rotated webhook credentials in that Vault file must be preserved. The rendered `/opt/hooks/proxy/config.json` is mode 0600, owned by UID 65532 so the nonroot Xray image can read it, and tasks handling it use `no_log`.

Use `ghcr.io/xtls/xray-core@sha256:d5c3729dfeed7c412af17a63b941c48c76ba68afee5b6184fe4078f8fa8bedcc` (official version 26.10.21) for a reproducible image. No host listener, TUN or global HTTP proxy environment variables. The same initial VLESS URI returned Reality verification errors with sing-box 1.14.4 on both local and auxiliary hosts, but official Xray returned HTTP 401 from Telegram on a synthetic token and HTTP 200 on the rotated real token from the VPS; one marked test send succeeded (message 7433). The owner's second active URI returned HTTP 401 through Xray locally, but not sing-box. Use Xray for both.

## Task 1: sidecar, preflight and local verification

1. Add tests to `ansible/tests/test_hooks.py` that render Compose with a synthetic proxy config and assert the Xray service has no published ports, the proxy network is shared only with `hooks`, the config mount is read-only, the webhook environment has `TELEGRAM_PROXY_URL`, and the sidecar image is digest-pinned. Test preflight rejects missing/invalid proxy config without printing secrets. Test rendered config mode 0600 and exact synthetic JSON content. Run these tests red before implementation.
2. Extend `roles/hooks/defaults/main.yml`, `tasks/validate.yml`, `tasks/main.yml`, `templates/compose.yml.j2`, and add `templates/xray.json.j2`. Validation requires an HTTP inbound on `0.0.0.0:2080`, a default first `blackhole` outbound, a VLESS `xtls-rprx-vision` outbound with TLS Reality fields matching the VLESS URI, and an exact route for `full:api.telegram.org` to VLESS. No `insecure` or alternative routing path. Validate before modifying the VPS. Keep existing hooks data and Caddy behavior.
3. Pull the public Xray image explicitly with the existing Compose project before `up`; do not change the anonymous `hooks` pull contract. Render config with `no_log: true`, mode 0600 and owner UID 65532, and no `--diff` disclosure.
4. Document the proxy route, secret location, test/rollback commands and failure modes in `ansible/README.md` and `RUNBOOK.md`. Use synthetic examples only.
5. Run `python3 -m unittest discover -s tests -v`, syntax-check, `ansible-lint` and `git diff --check`; run `xray run -test` against a synthetic rendered config and `docker compose config`.

## Task 2: controlled live integration

1. Controller only: parse the VLESS URI from a mode-0600 local file in memory, verify required `security=reality`, `type=tcp`, UUID, `pbk`, `sid`, `sni`, `flow`, `fp`; write the structured config into Ansible Vault without a plaintext intermediate file. Verify preservation of existing Vault keys by comparing hashes of their values before/after.
2. Controller only: test the actual Xray config with the pinned image, then deploy hooks role to auxiliary VPS while sender remains false. Verify Compose service health, config permissions, no published proxy port, and reachability of the foreign server.
3. Controller only: from the VPS, perform a TLS-verified Bot API `getMe` through the internal proxy using the rotated Vault token, logging only status and expected bot identity. Send one clearly marked test message into Apidog topic `6033` if `getMe` succeeds and verify the response. Do not print token-bearing URLs or raw HTTP errors.
4. Publish/review both repository PRs, merge when authorized by the existing session and rules, deploy the new webhook image by registry digest, and repeat the real proxy test from the new service container. Keep sender false pending the separate queue reconciliation and cutover gates in the webhook Task 10 plan.

## Review focus

- Secret text never appears in Git or tool output; failure paths fail closed.
- The proxy has no route to arbitrary destinations and is not reachable from public interfaces.
- Telegram proxy use is explicit; GitHub enrichment remains direct.
- Existing backup, queue and disabled worker are preserved.
