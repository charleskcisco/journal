#!/bin/bash

# enable compose key
export XKB_DEFAULT_OPTIONS="lv3:ralt_switch,compose:rctrl"

# Optional display rotation.
#   Leave DECK_ROTATE_OUTPUT empty for no rotation (landscape laptops /
#   netbooks with a normal screen). For a portrait writerdeck, set the
#   output to your panel (find it with `wlr-randr`) and a transform:
#     DECK_ROTATE_OUTPUT="HDMI-A-1"   # built-in laptop panels are often
#     DECK_ROTATE_TRANSFORM="90"      # LVDS-1 or eDP-1 instead
DECK_ROTATE_OUTPUT="${DECK_ROTATE_OUTPUT:-}"
DECK_ROTATE_TRANSFORM="${DECK_ROTATE_TRANSFORM:-90}"

# The '--' is the critical part here
cage foot -- sh -c "cd /path/to/journal && ./run.sh; exec bash" &

# Keep your existing wait loop
for i in {1..50}; do
    if [ -S "$WAYLAND_DISPLAY" ] || [ -S "/run/user/$(id -u)/wayland-0" ]; then
        break
    fi
    sleep 0.1
done

# Rotate only when an output is configured, and never let a missing
# wlr-randr or a wrong output name spam errors or take down the launch.
if [ -n "$DECK_ROTATE_OUTPUT" ]; then
    sleep 0.5
    if command -v wlr-randr >/dev/null 2>&1; then
        wlr-randr --output "$DECK_ROTATE_OUTPUT" \
                  --transform "$DECK_ROTATE_TRANSFORM" \
            || echo "start-deck: rotation failed (check DECK_ROTATE_OUTPUT name)."
    else
        echo "start-deck: wlr-randr not installed; skipping rotation."
    fi
fi

wait $!
