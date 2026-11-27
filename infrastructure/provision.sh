#!/bin/sh
# Owner-run production setup: `sudo ./provision.sh <step>`. See docs/m7-runbook.md.
# Runs markos_runtime.provision inside the runtime image, as root, against MARKOS_ETC.
set -eu
cd "$(dirname "$0")"
: "${MARKOS_IMAGE_TAG:?set MARKOS_IMAGE_TAG to the built image tag}"
etc="${MARKOS_ETC:-/etc/markos}"
tty=""
if [ -t 0 ]; then tty="-t"; fi
exec docker run --rm -i $tty --user 0:0 --read-only --tmpfs /tmp \
  --cap-drop ALL --cap-add CHOWN --cap-add FOWNER --cap-add DAC_OVERRIDE \
  -v "$etc:/etc/markos" -v "$PWD/migrations:/opt/migrations:ro" \
  --entrypoint /opt/runtime/.venv/bin/python \
  "markos-runtime:$MARKOS_IMAGE_TAG" -I -m markos_runtime.provision "$@"
