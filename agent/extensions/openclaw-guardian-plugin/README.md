# OpenClaw Guardian Plugin

This plugin uses the new OpenClaw SDK entry style and reports trust-layer payloads for the active hook points below:

- logs the payload to the official plugin logger
- pulls runtime toggles from `http://localhost:8001/open-apis/security_plugin/v1/openclaw_plugin/config`
- forwards detect requests to `http://localhost:8001/open-apis/security_plugin/v1/openclaw_plugin/detect`

- `llm_input`
- `llm_output`
- `before_tool_call`
- `after_tool_call`

The runtime config polling is managed by `api.registerService(...)`. The service initializes remote config on startup, keeps an in-memory snapshot for hook handlers, and disposes the refresh timer on shutdown.

## Files

- `openclaw.plugin.json`: native plugin manifest
- `src/index.ts`: plugin entry registered with `definePluginEntry`, hook registration, and `api.registerService(...)` wiring
- `src/runtime-config.ts`: remote config store plus polling service, typed against `OpenClawPluginApi` / `api.registerService(...)`
- `src/hook-payloads.ts`: hook-specific payload builders required by the trust-layer API
- `src/logger.ts`: safe payload formatter and logger helper
- `src/http-client.ts`: request body builder and detect/config service client
- `src/fetch-interceptor.ts`: optional `before_llm_fetch` / `after_llm_fetch` request-response interceptor, currently not enabled in `src/index.ts`
- `src/tool-effects.ts`: effect parser and hook-specific deny/rewrite handling

## Install

Link-install the plugin from this directory:

```bash
openclaw plugins install -l .
```

Then enable it in your OpenClaw config if needed:

```json5
{
  plugins: {
    entries: {
      "openclaw-guardian-plugin": {
        enabled: true
      }
    }
  }
}
```

Restart the Gateway after config changes.

## Notes

- The plugin logs via `api.logger.info(...)`.
- The plugin registers a background service via `api.registerService(...)`; the service typing is derived from `OpenClawPluginApi`, GETs remote runtime config on startup, retries up to 3 times, defaults to all hooks disabled on first-start failure, and refreshes the config every 60 seconds.
- The plugin POSTs `{ hook_name, payload }` to `http://localhost:8001/open-apis/security_plugin/v1/openclaw_plugin/detect`.
- Hook handlers read the latest in-memory runtime-config snapshot instead of fetching `/config` inline on every hook invocation.
- Hook payloads are sent as the trust-layer contract requires, such as tool/session fields for tool hooks and `domain` / `path` / `origin_req` or `origin_resp` for LLM fetch hooks.
- `llm_input` and `llm_output` are report-only hooks; deny/rewrite responses are logged but not applied.
- `after_tool_call` is also observe-only in runtime; detect decisions are logged but do not rewrite the actual hook return value.
- Payloads are serialized defensively so circular references and `Error` objects do not break logging.
- `before_llm_fetch` / `after_llm_fetch` support remains in `src/fetch-interceptor.ts`, but those hooks are not active until `installFetchInterceptor(api, configStore)` is enabled in `src/index.ts`.
