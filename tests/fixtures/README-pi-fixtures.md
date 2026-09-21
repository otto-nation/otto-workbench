# Pi backend fixtures

Captured from a live `pi` run, not hand-written. Everything the Pi backend gets
wrong today — `args` vs `arguments`, the `data` envelope around session stats,
two `message_end` costs in one prompt — was invisible to hand-built fixtures and
obvious against these. A fixture someone invents agrees with the code that reads
it; that is the loop these exist to break.

Both were captured against `google-vertex-claude`, which is what this machine
authenticates to via ambient gcloud ADC. A recapture on another provider is fine
— the field *shapes* are Pi's, not the provider's.

## `pi_prompt_session.jsonl`

`pi -p --mode json` output for a prompt that uses a tool, so it carries two
assistant messages and two separate `message_end` costs. A single-turn capture
would let "take the last cost" pass, which is the bug the sum is there to avoid.

```sh
mkdir -p /tmp/pitest && cd /tmp/pitest && echo hello > f.txt
pi -p --mode json --provider google-vertex-claude --model haiku \
  "Read the file f.txt and tell me its contents" > pi_prompt_session.jsonl
```

## `pi_rpc_stats_response.json`

One `get_session_stats` response from Pi's RPC mode, envelope included. The
envelope is the point: `tokens` and `cost` live under `data`, and reading them
from the top level yields zeroes that look like a free call.

```sh
printf '%s\n' '{"type":"prompt","message":"hi"}' '{"type":"get_session_stats"}' \
  | pi --mode rpc --provider google-vertex-claude --model haiku \
  | grep get_session_stats
```
